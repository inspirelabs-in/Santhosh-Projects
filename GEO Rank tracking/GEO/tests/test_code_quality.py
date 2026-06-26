"""
Guard tests — catch bug classes that static analysis misses.
Run with: python -m pytest tests/ -v
"""
import ast
import importlib
import pkgutil
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# ── SQL: %s inside single-quoted literals ────────────────────────
# psycopg3 uses server-side binding; %s inside a SQL string literal
# is never parameterised — it becomes a literal '%s' and the query
# either errors or silently does the wrong thing.

_QUOTED_PLACEHOLDER = re.compile(r"'[^']*%s[^']*'")


def _python_files():
    return sorted(APP_ROOT.rglob("*.py"))


def test_no_parameterised_placeholder_inside_sql_quotes():
    """Ensure no %s appears inside single-quoted SQL string literals."""
    violations = []
    for py in _python_files():
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if _QUOTED_PLACEHOLDER.search(line):
                violations.append(f"{py.relative_to(APP_ROOT.parent)}:{i}  {line.strip()}")
    assert not violations, (
        "Found %s inside SQL single-quotes (psycopg3 won't parameterise these):\n"
        + "\n".join(violations)
    )


# ── Import smoke test ────────────────────────────────────────────
# Catches bad imports, missing names, and circular imports at test
# time rather than at 3 AM in production.

def test_all_app_modules_import():
    """Every module under app/ must import without error."""
    failures = []
    for info in pkgutil.walk_packages([str(APP_ROOT)], prefix="app."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            failures.append(f"{info.name}: {exc}")
    assert not failures, "Modules failed to import:\n" + "\n".join(failures)
