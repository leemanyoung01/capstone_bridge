"""Inspect debug/rep_candidates_<keyword>.json for specific image_ids per axis.

Usage: python scripts/_inspect_candidates.py 짜장면 양많음 4274101 4279301 ...
"""
import json
import sys
from collections import defaultdict

if len(sys.argv) < 3:
    print("usage: _inspect_candidates.py <keyword> <axis> [image_id ...]")
    sys.exit(1)

keyword = sys.argv[1]
axis = sys.argv[2]
ids = set(int(x) for x in sys.argv[3:]) if len(sys.argv) > 3 else None

path = f"debug/rep_candidates_{keyword}.json"
data = json.load(open(path, encoding="utf-8"))

# Print axis-level thresholds
thr = data.get("thresholds", {})
print(f"=== keyword={keyword}  axis={axis} ===")
print("global thresholds:", json.dumps(thr, ensure_ascii=False))

# Find axis priority / order
print("axis_priority:", data.get("axis_priority"))
print("axis_order:   ", data.get("axis_order"))

# Selected
print("\nSELECTED:")
for s in data.get("selected_images", []):
    if s.get("axis") == axis or axis == "*":
        print(f"  axis={s.get('axis')} score={s.get('score')} url={s.get('image_url','')}")
        bd = s.get("score_breakdown", {})
        print(f"     image_axis={bd.get('image_axis_score')} presence={bd.get('axis_presence_score')} wrong_visual={bd.get('axis_wrong_visual_score')} cat={bd.get('category_score')} wrong={bd.get('wrong_food_penalty')}")

# Top candidates per axis
buckets = data.get("top_candidates_by_axis") or {}
cands = buckets.get(axis) or []
print(f"\nTOP CANDIDATES for {axis} (count={len(cands)}):")
for c in cands[:15]:
    image_url = c.get("image_url", "") or ""
    image_id = None
    if image_url:
        try:
            image_id = int(image_url.rsplit("/", 1)[-1].split(".")[0])
        except Exception:
            pass
    bd = c.get("score_breakdown", {})
    print(f"  id={image_id} score={c.get('score'):.4f} clip={bd.get('image_axis_score')} presence={bd.get('axis_presence_score')} wrong_visual={bd.get('axis_wrong_visual_score')} reason={c.get('reason','')}")
    print(f"     url={image_url}")

# If user provided ids, search rejections and evaluation_axes
if ids:
    print(f"\nDESIRED IMAGES (ids={sorted(ids)}):")
    # We need to find them in debug_multimodal_*.json image_axis_evaluations because this report only top-10/axis
    eval_path = f"debug_multimodal_{keyword}.json"
    try:
        full = json.load(open(eval_path, encoding="utf-8"))
        evals = full.get("image_axis_evaluations", [])
    except Exception as e:
        print("could not load full debug:", e)
        evals = []
    by_id_axis = defaultdict(dict)
    for e in evals:
        iid = int(e.get("image_id") or 0)
        if iid in ids:
            by_id_axis[iid][e.get("axis")] = e
    for iid in sorted(ids):
        e = by_id_axis.get(iid, {}).get(axis)
        if e is None:
            print(f"  id={iid}: NOT FOUND for axis={axis}")
            continue
        bd = e.get("score_breakdown", {})
        print(f"  id={iid} score={e.get('final_score')} reason={e.get('excluded_reason')}")
        print(f"     clip={bd.get('image_axis_score')} presence={bd.get('axis_presence_score')} wrong_visual={bd.get('axis_wrong_visual_score')} cat={bd.get('category_score')} wrong={bd.get('wrong_food_penalty')} food={bd.get('food_presence_score')}")
