"""기존 debug_multimodal_짜장면.json의 raw CLIP 점수에 새 가중치를 대입해
   사용자가 원하는 desired 이미지가 어느 정도 올라올지 추정.

CAUTION: axis_clip / axis_presence / axis_wrong_visual 자체는 새 prompt로 바뀌면
   실제 값이 달라진다. 여기 시뮬레이션은 '동일 raw 값' 기준이므로 최소 변화 예상.
"""
import json
from collections import defaultdict

DEBUG = json.load(open("debug_multimodal_짜장면.json", encoding="utf-8"))
RULES = json.load(open("stand/stand_rules.json", encoding="utf-8"))

img_filt = RULES["image_filtering"]
ovr = img_filt["axis_score_overrides"]["짜장면"]
default = ovr.get("_default", {})


def cfg(axis: str, key: str, fallback):
    if axis in ovr and key in ovr[axis]:
        return ovr[axis][key]
    if key in default:
        return default[key]
    return fallback


desired = {
    "양많음": [4274101, 4279301, 4306100, 4305502],
    "감칠맛": [4177400, 4191503, 4291402, 4186503],
    "소스농도": [4191200, 4193000, 4194203, 4240204, 4252601],
    "면컨디션": [4196102, 4196105, 4246502, 4248603, 4288400, 4289702],
}

# Pull base thresholds
th = img_filt.get("thresholds", {})
w_food = float(th.get("food_presence_weight", 1.0))
w_non = float(th.get("non_food_penalty_weight", 0.6))
w_cat = float(th.get("category_bonus_weight", 0.3))
w_wrong = float(th.get("wrong_food_weight", 0.7))

# Per restaurant cap
gate = img_filt["representative_gate_thresholds"]["짜장면"]
max_per_rest = gate.get("max_per_restaurant", th.get("max_per_restaurant", 2))


def recompute(rec, axis):
    bd = rec.get("score_breakdown") or {}
    axis_clip = bd.get("image_axis_score", 0)
    presence = bd.get("axis_presence_score", 0)
    wrong_visual = bd.get("axis_wrong_visual_score", 0)
    cat = bd.get("category_score", rec.get("category_presence_score", 0))
    food = bd.get("food_presence_score", rec.get("food_presence_score", 0))
    non = bd.get("non_food_penalty", rec.get("non_food_penalty", 0))
    wrong = bd.get("wrong_food_penalty", rec.get("wrong_food_penalty", 0))

    w_clip = float(cfg(axis, "image_axis_weight", 2.8))
    w_pres = float(cfg(axis, "axis_presence_weight", 1.6))
    w_wv = float(cfg(axis, "axis_wrong_weight", 2.2))
    max_wv = cfg(axis, "max_axis_wrong_visual", None)
    min_pres = cfg(axis, "min_axis_presence", None)

    reject = None
    if max_wv is not None and wrong_visual > max_wv:
        reject = f"wrong_visual {wrong_visual:.3f} > {max_wv}"
    elif min_pres is not None and presence < min_pres:
        reject = f"presence {presence:.3f} < {min_pres}"

    score = (w_clip * axis_clip + w_pres * presence + w_cat * cat + w_food * food
             - w_non * non - w_wrong * wrong - w_wv * wrong_visual)
    return score, reject


by_axis_top = defaultdict(list)
for rec in DEBUG.get("image_axis_evaluations", []):
    if rec.get("excluded_reason") in ("rejected_low_axis_clip",
                                       "rejected_low_axis_presence",
                                       "rejected_axis_wrong_visual",
                                       None):
        axis = rec.get("axis")
        if axis not in desired:
            continue
        score, reject = recompute(rec, axis)
        by_axis_top[axis].append({
            "image_id": rec.get("image_id"),
            "restaurant_id": rec.get("restaurant_id"),
            "rest_name": rec.get("restaurant_name"),
            "score": score,
            "reject": reject,
            "in_desired": int(rec.get("image_id") or -1) in desired.get(axis, []),
        })

print("=== Projected rank (with new weights, OLD prompt CLIP raw) ===\n")
print("Note: actual axis_clip/presence/wrong_visual will shift once new prompts run.")
print("This is just for guessing relative positions.\n")

for axis, items in by_axis_top.items():
    items.sort(key=lambda x: -x["score"])
    print(f"--- {axis} (top 12, max_per_restaurant={max_per_rest}) ---")
    seen_rest = set()
    rank = 0
    for it in items[:25]:
        if it["restaurant_id"] in seen_rest:
            tag = "[restaurant_cap_skip]"
        elif it["reject"]:
            tag = f"[reject: {it['reject']}]"
        else:
            tag = "[ok]" if not it["in_desired"] else "[OK + DESIRED]"
            seen_rest.add(it["restaurant_id"])
            rank += 1
        if rank >= 12:
            break
        marker = "★ " if it["in_desired"] else "  "
        print(f"  {marker}id={it['image_id']:>14} rest={it['restaurant_id']:>4} score={it['score']:.4f} {tag}")
    print()

print("=== Desired images per axis with new gates ===")
for axis, ids in desired.items():
    print(f"--- {axis} ---")
    for iid in ids:
        for rec in DEBUG.get("image_axis_evaluations", []):
            if rec.get("axis") == axis and int(rec.get("image_id") or 0) == iid:
                score, reject = recompute(rec, axis)
                print(f"  id={iid} rest={rec.get('restaurant_id')} score={score:.4f} reject={reject}")
                break
        else:
            print(f"  id={iid} (no record)")
    print()
