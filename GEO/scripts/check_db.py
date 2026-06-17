"""Quick DB stats check."""
import psycopg
from psycopg.rows import dict_row

conn = psycopg.connect(
    "postgresql://postgres:postgres@localhost:5432/grabon_geo",
    row_factory=dict_row,
    connect_timeout=10,
)

rows = conn.execute(
    "SELECT tier, is_canonical, COUNT(*) as cnt FROM prompts GROUP BY tier, is_canonical ORDER BY tier, is_canonical"
).fetchall()
for r in rows:
    print(f"Tier {r['tier']}, canonical={r['is_canonical']}: {r['cnt']}")

total = conn.execute("SELECT COUNT(*) as c FROM prompts").fetchone()["c"]
groups = conn.execute(
    "SELECT COUNT(DISTINCT keyword_group) as c FROM prompts WHERE keyword_group IS NOT NULL"
).fetchone()["c"]
never_run = conn.execute(
    "SELECT COUNT(*) as c FROM prompts WHERE last_run_at IS NULL"
).fetchone()["c"]

print(f"\nTotal: {total}")
print(f"Keyword groups: {groups}")
print(f"Never run: {never_run}")

# Sample tier 1 canonical
print("\nTier 1 canonical (sample):")
t1 = conn.execute(
    "SELECT text, merchant_category, keyword_group FROM prompts WHERE tier = 1 AND is_canonical = TRUE LIMIT 15"
).fetchall()
for r in t1:
    print(f"  [{r['merchant_category']}] {r['text']} (group: {r['keyword_group']})")

conn.close()
