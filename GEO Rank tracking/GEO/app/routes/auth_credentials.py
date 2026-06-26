"""Credential encryption, CRUD API routes, and legacy migration."""
import base64
import hashlib
import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from cryptography.fernet import Fernet
from app.database import run_db
from app.config import get_settings

router = APIRouter()
log = logging.getLogger("geo.auth.credentials")


class CredentialInput(BaseModel):
    provider: str
    email: str
    password: str
    slot_index: int | None = None


class OTPInput(BaseModel):
    code: str


# ── Encryption ───────────────────────────────────────────

def _get_fernet() -> Fernet:
    settings = get_settings()
    key = hashlib.sha256(settings.credentials_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


# ── CRUD Routes ──────────────────────────────────────────

@router.post("/api/auth/credentials")
async def save_credentials(cred: CredentialInput):
    """Save encrypted credentials for an engine. Supports multi-slot via slot_index."""
    from app.routes.auth import LOGIN_PROVIDERS
    from app.routes.auth_auto_login import AUTO_LOGIN_FLOWS
    if cred.provider not in LOGIN_PROVIDERS and cred.provider not in AUTO_LOGIN_FLOWS:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": f"Unknown provider: {cred.provider}"}, status_code=400)
    encrypted_email = _encrypt(cred.email)
    encrypted_password = _encrypt(cred.password)
    payload = json.dumps({"email": encrypted_email, "password": encrypted_password})

    if cred.slot_index is not None:
        slot_idx = cred.slot_index
    else:
        slot_idx = await _next_free_cred_slot(cred.provider, cred.email)

    db_key = f"creds_{cred.provider}_{slot_idx}"

    def _save(conn):
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()
        """, (db_key, payload, payload))
        conn.commit()
    await run_db(_save)
    log.info(f"Credentials saved for {cred.provider} slot {slot_idx}")
    return {"status": "saved", "provider": cred.provider, "slot_index": slot_idx}


async def _next_free_cred_slot(provider: str, email: str, max_slots: int = 10) -> int:
    """Find existing slot for this email, or next free slot index."""
    def _scan(conn):
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE %s ORDER BY key",
            (f"creds_{provider}_%",),
        ).fetchall()
        return rows

    rows = await run_db(_scan)

    for r in rows:
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            existing_email = _decrypt(data["email"])
            if existing_email.lower() == email.lower():
                return int(r["key"].rsplit("_", 1)[-1])
        except Exception:
            continue

    used_indices = set()
    for r in rows:
        try:
            idx = int(r["key"].rsplit("_", 1)[-1])
            used_indices.add(idx)
        except ValueError:
            continue

    for i in range(max_slots):
        if i not in used_indices:
            return i
    return len(used_indices)


@router.get("/api/auth/credentials")
async def list_credentials():
    """List all credential slots per provider with masked emails."""
    def _list(conn):
        rows = conn.execute(
            "SELECT key, value, updated_at::text FROM app_settings WHERE key LIKE 'creds_%%' ORDER BY key"
        ).fetchall()
        return rows

    rows = await run_db(_list)
    result = {}
    for r in rows:
        key = r["key"]
        parts = key.split("_", 1)
        if len(parts) < 2:
            continue
        rest = parts[1]

        slot_parts = rest.rsplit("_", 1)
        try:
            slot_index = int(slot_parts[-1])
            provider = slot_parts[0] if len(slot_parts) > 1 else rest
        except ValueError:
            provider = rest
            slot_index = 0

        masked_email = ""
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            email = _decrypt(data["email"])
            ep = email.split("@")
            if len(ep) == 2:
                name = ep[0]
                masked_email = name[:3] + "***@" + ep[1] if len(name) > 3 else name[0] + "***@" + ep[1]
            else:
                masked_email = email[:4] + "***"
        except Exception:
            masked_email = "***"

        if provider not in result:
            result[provider] = {"accounts": [], "has_credentials": True}
        result[provider]["accounts"].append({
            "slot_index": slot_index,
            "masked_email": masked_email,
            "updated_at": r["updated_at"],
            "db_key": key,
        })

    return {"credentials": result}


@router.delete("/api/auth/credentials/{provider}")
async def delete_credentials(provider: str, slot_index: int = 0):
    """Remove stored credentials for a specific slot."""
    db_key = f"creds_{provider}_{slot_index}"

    def _del(conn):
        conn.execute("DELETE FROM app_settings WHERE key = %s", (db_key,))
        conn.commit()
    await run_db(_del)
    return {"status": "deleted", "provider": provider, "slot_index": slot_index}


# ── Credential Loading ───────────────────────────────────

async def load_credentials(provider: str, slot_index: int | None = None) -> dict | None:
    """Load and decrypt credentials. If slot_index given, load that slot.
    Otherwise load first available slot (creds_{provider}_0, _1, etc).
    Falls back to legacy creds_{provider} key."""
    if slot_index is not None:
        keys_to_try = [f"creds_{provider}_{slot_index}"]
    else:
        keys_to_try = [f"creds_{provider}_{i}" for i in range(10)]
    keys_to_try.append(f"creds_{provider}")

    def _load(conn):
        for k in keys_to_try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s", (k,)
            ).fetchone()
            if row:
                return row["value"]
        return None
    raw = await run_db(_load)
    if not raw:
        return None
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw)
        return {
            "email": _decrypt(data["email"]),
            "password": _decrypt(data["password"]),
        }
    except Exception as e:
        log.error(f"Failed to decrypt credentials for {provider}: {e}")
        return None


async def load_all_credentials(provider: str) -> list[dict]:
    """Load all credential slots for a provider. Returns list of {email, password, slot_index}."""
    def _load_all(conn):
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE %s ORDER BY key",
            (f"creds_{provider}_%",),
        ).fetchall()
        legacy = conn.execute(
            "SELECT key, value FROM app_settings WHERE key = %s",
            (f"creds_{provider}",),
        ).fetchone()
        result = list(rows)
        if legacy:
            result.append(legacy)
        return result

    rows = await run_db(_load_all)
    creds_list = []
    seen_emails = set()
    for r in rows:
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            email = _decrypt(data["email"])
            if email.lower() in seen_emails:
                continue
            seen_emails.add(email.lower())
            key = r["key"]
            try:
                slot_idx = int(key.rsplit("_", 1)[-1])
            except ValueError:
                slot_idx = 0
            creds_list.append({
                "email": email,
                "password": _decrypt(data["password"]),
                "slot_index": slot_idx,
            })
        except Exception:
            continue
    return creds_list


# ── Legacy Migration ─────────────────────────────────────

async def migrate_legacy_credentials():
    """Migrate legacy creds_X keys to creds_X_0 format. Idempotent."""
    providers = ["google", "chatgpt", "claude", "perplexity", "gemini"]

    def _migrate(conn):
        migrated = []
        for p in providers:
            old_key = f"creds_{p}"
            new_key = f"creds_{p}_0"
            old_row = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s", (old_key,)
            ).fetchone()
            if not old_row:
                continue
            existing_new = conn.execute(
                "SELECT key FROM app_settings WHERE key = %s", (new_key,)
            ).fetchone()
            if existing_new:
                continue
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
            """, (new_key, json.dumps(old_row["value"]) if not isinstance(old_row["value"], str) else old_row["value"]))
            conn.execute("DELETE FROM app_settings WHERE key = %s", (old_key,))
            migrated.append(p)
        if migrated:
            conn.commit()
        return migrated

    migrated = await run_db(_migrate)
    if migrated:
        log.info(f"Migrated legacy credentials to slot format: {migrated}")
