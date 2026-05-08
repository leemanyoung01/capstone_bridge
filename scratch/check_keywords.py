import db
import json

with db.get_conn() as conn:
    keywords = db.get_all_rep_keywords(conn)
    print(json.dumps(keywords, ensure_ascii=False))
