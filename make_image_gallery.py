# make_image_gallery.py
import argparse
import html
import os
import urllib.parse
from pathlib import Path

from db import get_conn


def make_gallery(keyword: str, limit: int = 1000):
    encoded_kw = urllib.parse.quote(keyword, safe="")
    patterns = [
        f"%/reviews/{keyword}/%",
        f"%/reviews/{encoded_kw}/%",
    ]

    sql = """
        SELECT
            ri.image_id,
            ri.image_url,
            ri.image_path,
            v.review_id,
            v.content AS review_text,
            r.restaurant_id,
            r.name AS restaurant_name
        FROM review_images ri
        JOIN reviews v ON v.review_id = ri.review_id
        JOIN restaurants r ON r.restaurant_id = v.restaurant_id
        WHERE (ri.image_url ILIKE %s OR ri.image_url ILIKE %s)
        ORDER BY r.name, v.review_id, ri.image_id
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (patterns[0], patterns[1], limit))
            rows = cur.fetchall()

    cards = []
    for row in rows:
        image_url = row.get("image_url") or ""
        review_text = (row.get("review_text") or "")[:120]
        restaurant = row.get("restaurant_name") or ""
        image_id = row.get("image_id")
        review_id = row.get("review_id")

        cards.append(f"""
        <div class="card">
          <img src="{html.escape(image_url)}" loading="lazy" />
          <div class="meta">
            <b>{html.escape(restaurant)}</b><br/>
            image_id: {image_id} / review_id: {review_id}<br/>
            <button onclick="copyText('{html.escape(image_url)}')">URL 복사</button>
            <button onclick="copyBlock('{html.escape(image_url)}')">JSON 조각 복사</button>
          </div>
          <p>{html.escape(review_text)}</p>
          <textarea readonly>{html.escape(image_url)}</textarea>
        </div>
        """)

    out = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>{keyword} 이미지 갤러리</title>
<style>
body {{
  font-family: Arial, sans-serif;
  background: #f7f7f5;
  padding: 24px;
}}
h1 {{
  margin-bottom: 8px;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 18px;
}}
.card {{
  background: white;
  border-radius: 14px;
  padding: 12px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.08);
}}
.card img {{
  width: 100%;
  height: 180px;
  object-fit: cover;
  border-radius: 10px;
  background: #eee;
}}
.meta {{
  font-size: 13px;
  margin-top: 10px;
  line-height: 1.5;
}}
p {{
  font-size: 12px;
  color: #555;
  min-height: 36px;
}}
textarea {{
  width: 100%;
  height: 42px;
  font-size: 11px;
}}
button {{
  margin-top: 6px;
  margin-right: 4px;
  border: 1px solid #ddd;
  border-radius: 999px;
  padding: 5px 9px;
  background: #fafafa;
  cursor: pointer;
}}
</style>
</head>
<body>
<h1>{keyword} 이미지 갤러리</h1>
<p>총 {len(rows)}장. 마음에 드는 사진의 URL을 복사해서 curated JSON에 넣으면 됨.</p>
<div class="grid">
{''.join(cards)}
</div>

<script>
function copyText(text) {{
  navigator.clipboard.writeText(text);
}}
function copyBlock(url) {{
  const block = `{{\\n  "rank": 1,\\n  "axis": "국물맛",\\n  "label": "국물맛",\\n  "image_url": "${{url}}",\\n  "review_snippet": ""\\n}}`;
  navigator.clipboard.writeText(block);
}}
</script>
</body>
</html>
"""

    filename = f"gallery_{keyword}.html"
    Path(filename).write_text(out, encoding="utf-8")
    print(f"✅ 생성 완료: {filename}")
    print(f"이미지 {len(rows)}장")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("keyword")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    make_gallery(args.keyword, args.limit)