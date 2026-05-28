from db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT keyword,
                   COUNT(*)                  AS rows,
                   COUNT(semantic_vector)    AS has_semantic,
                   COUNT(semantic_evidence)  AS has_evidence
            FROM restaurant_vectors
            GROUP BY keyword
            ORDER BY keyword
            """
        )
        print(f'{"keyword":<14}{"rows":>6}{"semantic":>10}{"evidence":>10}')
        print("-" * 40)
        for r in cur.fetchall():
            k = r["keyword"] or "(null)"
            print(f'{k:<14}{r["rows"]:>6}{r["has_semantic"]:>10}{r["has_evidence"]:>10}')
