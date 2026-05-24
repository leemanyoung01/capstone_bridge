import json
import sys

keyword = sys.argv[1] if len(sys.argv) > 1 else "짜장면"
path = f"{keyword}_representative_images.json"
data = json.load(open(path, encoding="utf-8"))
print(f"keyword: {data.get('keyword')}")
print(f"images: {len(data.get('images', []))}")
for img in data.get("images", []):
    axis = img.get("axis", "")
    score = img.get("score") or 0
    image_id = img.get("image_id")
    rest = img.get("restaurant_name", "") or ""
    url = img.get("image_url", "") or ""
    review = (img.get("review_snippet") or "")[:60]
    print(f"  axis={axis:>10}  score={score:.4f}  id={image_id}  rest={rest[:20]}  url={url[:80]}")
    if review:
        print(f"      review: {review}")
