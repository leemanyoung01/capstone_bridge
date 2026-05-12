import db
import json
import sys

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

with db.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT keyword, axis, label, image_url, restaurant_id FROM representative_images WHERE keyword LIKE '%청국장%'")
        rows = cur.fetchall()
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
