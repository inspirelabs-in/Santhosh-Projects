# grabon-intel

Next-gen autonomous lead intelligence for Grabon. Phase-1 deliverables:

- New Postgres schema (signals, dossiers, opportunities, approvals, agent_traces, budget)
- Async SQLAlchemy 2.0 ORM
- Signal collector framework + first source: Meta Ad Library

## Quick start

```powershell
cd "D:/Lead gen/grabon_intel"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
cp .env.example .env  # edit DB URL + META_AD_LIBRARY_TOKEN

# enable pgvector + timescaledb extensions in target DB (one-time)
psql $env:GRABON_DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS timescaledb;"

# migrate
alembic upgrade head

# collect
grabon-intel collect meta-ads --country IN --search-terms "skincare" --limit 100

# test
pytest
```

## Layout

```
grabon_intel/
  config.py        # pydantic-settings, env-driven
  db/
    __init__.py    # async engine + session factory
    models.py      # SQLAlchemy 2.0 ORM
  signals/
    base.py        # Collector ABC
    meta_ad_library.py
  resolver.py      # brand hint → canonical brand_id (stub for now)
  cli.py           # Typer entry
alembic/
  env.py
  versions/001_init.py
tests/
```

## Notes

- Reuses existing `brands` table from `../leadgen/leadgen_pkg`. Migration is additive — does not alter existing columns beyond adding `root_domain` generated column.
- Timescale extension optional; if absent, `signals` falls back to plain table with BRIN index on `observed_at`.
- pgvector required for dossier embeddings (later phase). Migration creates extension if missing.
