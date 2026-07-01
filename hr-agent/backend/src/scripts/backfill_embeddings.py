"""Backfill embeddings for all candidate_profiles missing them.

Usage:
    cd backend
    python -m src.scripts.backfill_embeddings [--batch-size 50] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select, func

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def backfill(batch_size: int = 50, dry_run: bool = False) -> int:
    import litellm

    from src.config import get_settings
    from src.db.base import CandidateProfileRow
    from src.db.connection import session_scope

    settings = get_settings()
    model = settings.embedding_model or "text-embedding-3-large"
    updated = 0

    async with session_scope() as session:
        total_missing = (
            await session.execute(
                select(func.count(CandidateProfileRow.id)).where(
                    CandidateProfileRow.embedding.is_(None)
                )
            )
        ).scalar_one()
    logger.info("Found %d profiles without embeddings", total_missing)

    if dry_run:
        logger.info("Dry run — exiting without changes")
        return 0

    offset = 0
    while True:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(CandidateProfileRow)
                    .where(CandidateProfileRow.embedding.is_(None))
                    .order_by(CandidateProfileRow.created_at.asc())
                    .limit(batch_size)
                )
            ).scalars().all()

            if not rows:
                break

            texts = []
            row_ids = []
            for row in rows:
                pd = row.parsed_data or {}
                name = pd.get("name") or ""
                headline = pd.get("headline") or ""
                raw_skills = pd.get("skills") or pd.get("top_skills") or []
                if isinstance(raw_skills, list):
                    skills = ", ".join(raw_skills)
                elif isinstance(raw_skills, str):
                    skills = raw_skills
                else:
                    skills = ""
                summary = pd.get("summary") or ""
                embed_text = f"{name}. {headline}. Skills: {skills}. {summary}"
                if len(embed_text.strip()) <= 20:
                    continue
                texts.append(embed_text[:8000])
                row_ids.append(row.id)

            if not texts:
                break

            try:
                resp = await litellm.aembedding(model=model, input=texts, dimensions=256)
                for i, item in enumerate(resp.data):
                    embedding = item["embedding"]
                    row = next(r for r in rows if r.id == row_ids[i])
                    row.embedding = embedding
                    updated += 1
                await session.flush()
            except Exception:
                logger.exception("Batch embedding failed at offset %d", offset)
                break

        offset += batch_size
        logger.info("Progress: %d/%d profiles updated", updated, total_missing)

    logger.info("Backfill complete: %d profiles updated", updated)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill candidate profile embeddings")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    count = asyncio.run(backfill(batch_size=args.batch_size, dry_run=args.dry_run))
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
