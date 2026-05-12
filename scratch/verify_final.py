import db, sys
sys.stdout.reconfigure(encoding='utf-8')
with db.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT name, category FROM restaurants WHERE name ILIKE '%조개창고%'")
        for row in cur.fetchall():
            print(f"Name: {row['name']}, Category: {row['category']}")
