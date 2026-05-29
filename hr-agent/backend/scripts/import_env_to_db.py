"""One-shot importer: copy current .env values into config_settings.

Idempotent — running twice has no effect when values already match. Only
non-default values are migrated; environment defaults remain in env defaults.

Usage:
    python -m scripts.import_env_to_db
    python -m scripts.import_env_to_db --dry-run

After importing, the corresponding .env entries can be removed from the
deployed stack — the DB row takes precedence on every read.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from src.config import _build_settings  # type: ignore[attr-defined]
from src.config_schema import FIELDS

logger = logging.getLogger(__name__)


async def import_env(dry_run: bool = False) -> int:
    base = _build_settings()

    candidates: dict[str, Any] = {}
    for f in FIELDS:
        cur = getattr(base, f.settings_attr, None)
        if cur is None:
            continue
        if cur == f.default:
            continue
        # Skip empty strings: those are env-style "unset".
        if isinstance(cur, str) and not cur.strip():
            continue
        candidates[f.key] = cur

    if not candidates:
        print("Nothing to import. All values match defaults.")
        return 0

    print(f"Found {len(candidates)} non-default values to import:")
    for key in sorted(candidates):
        f = next(x for x in FIELDS if x.key == key)
        marker = "***" if f.is_secret else repr(candidates[key])[:60]
        print(f"  {key:<40s} = {marker}")

    if dry_run:
        print("\nDry run. No changes written.")
        return 0

    from src.services import config_store

    stored = await config_store.set_values(
        candidates,
        actor="import_env_script",
        actor_role="admin",
    )
    print(f"\nWrote {len(stored)} settings to config_settings.")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(import_env(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
