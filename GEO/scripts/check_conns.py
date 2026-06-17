"""Check and terminate stale DB connections."""
import sys
import psycopg

conn = psycopg.connect("postgresql://postgres:postgres@localhost:5432/grabon_geo", connect_timeout=10)
rows = conn.execute(
    "SELECT pid, state, query_start, query FROM pg_stat_activity WHERE datname = 'grabon_geo' AND pid != pg_backend_pid()"
).fetchall()
print(f"Active connections: {len(rows)}")
for r in rows:
    print(f"  pid={r[0]} state={r[1]} query={str(r[3])[:80]}")

if "--kill" in sys.argv and rows:
    for r in rows:
        conn.execute("SELECT pg_terminate_backend(%s)", (r[0],))
    conn.commit()
    print(f"Terminated {len(rows)} connections")

conn.close()
