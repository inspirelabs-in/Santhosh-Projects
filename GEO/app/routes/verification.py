"""
Verification routes -- CRUD for applied fixes + verification triggers + timeline data.
"""
import logging
from fastapi import APIRouter

from app.database import run_db

log = logging.getLogger("geo.routes.verification")

router = APIRouter(prefix="/api/fixes")


@router.post("")
async def create_fix(
    diagnosis_id: str | None = None,
    prompt_id: str | None = None,
    engine_name: str = "all",
    description: str = "",
    target_url: str = "",
    applied_at: str | None = None,
):
    """Record that a fix was applied based on a diagnosis."""
    if not prompt_id and not diagnosis_id:
        return {"error": "Provide prompt_id or diagnosis_id"}

    if diagnosis_id and not prompt_id:
        diag = await run_db(lambda conn: conn.execute(
            "SELECT prompt_id, engine_name FROM diagnoses WHERE id = %s::uuid",
            (diagnosis_id,),
        ).fetchone())
        if not diag:
            return {"error": "Diagnosis not found"}
        prompt_id = str(diag["prompt_id"])
        engine_name = diag["engine_name"]

    def _insert(conn):
        diag_param = diagnosis_id
        at_param = applied_at if applied_at else None
        cur = conn.execute(
            """INSERT INTO applied_fixes
                (diagnosis_id, prompt_id, engine_name, description, target_url, applied_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
                RETURNING id, applied_at""",
            (diag_param, prompt_id, engine_name, description, target_url, at_param),
        )
        row = cur.fetchone()
        conn.commit()
        return row

    row = await run_db(_insert)
    if not row:
        return {"error": "Insert failed"}
    return {
        "id": str(row["id"]),
        "prompt_id": prompt_id,
        "applied_at": str(row["applied_at"]),
        "status": "monitoring",
    }


@router.get("")
async def list_fixes(status: str | None = None, limit: int = 50):
    """List all applied fixes with verification status."""
    def _q(conn):
        base = """
            SELECT af.id, af.prompt_id, af.diagnosis_id, af.engine_name,
                   af.description, af.target_url, af.applied_at,
                   af.verification_status, af.last_verified_at,
                   af.created_at, p.text as keyword
            FROM applied_fixes af
            JOIN prompts p ON af.prompt_id = p.id
        """
        params = []
        if status:
            base += " WHERE af.verification_status = %s"
            params.append(status)
        base += " ORDER BY af.applied_at DESC LIMIT %s"
        params.append(limit)
        return conn.execute(base, params).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.get("/{fix_id}")
async def get_fix(fix_id: str):
    """Get fix details with latest verification result."""
    row = await run_db(lambda conn: conn.execute(
        """SELECT af.*, p.text as keyword
           FROM applied_fixes af
           JOIN prompts p ON af.prompt_id = p.id
           WHERE af.id = %s::uuid""",
        (fix_id,),
    ).fetchone())
    if not row:
        return {"error": "Fix not found"}
    return dict(row)


@router.post("/{fix_id}/verify")
async def verify_fix(fix_id: str):
    """Trigger on-demand verification for a fix."""
    from app.agent.verification import check_fix_impact
    result = await check_fix_impact(fix_id)
    return result


@router.get("/{fix_id}/timeline")
async def fix_timeline(fix_id: str):
    """Get before/after time-series data for visualization."""
    from app.agent.verification import get_fix_timeline
    return await get_fix_timeline(fix_id)


@router.delete("/{fix_id}")
async def delete_fix(fix_id: str):
    """Delete an applied fix record."""
    def _delete(conn):
        conn.execute("DELETE FROM applied_fixes WHERE id = %s::uuid", (fix_id,))
        conn.commit()
    await run_db(_delete)
    return {"status": "deleted"}
