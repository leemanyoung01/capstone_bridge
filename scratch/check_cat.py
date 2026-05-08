import db
with db.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT name, category FROM restaurants WHERE name ILIKE '%포케박스%'")
        for row in cur.fetchall():
            print(f"Name: {row['name']}, Category: {row['category']}")
