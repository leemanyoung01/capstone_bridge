"""면컨디션 desired가 1위가 될 수 있는지 시뮬레이션.
이전 debug의 raw 점수(다른 prompts였음)에 새 가중치 + 새 wrong gate를 대입."""
import json
from collections import defaultdict

DEBUG = json.load(open("debug_multimodal_짜장면.json", encoding="utf-8"))
RULES = json.load(open("stand/stand_rules.json", encoding="utf-8"))
imgf = RULES["image_filtering"]
ovr = imgf["axis_score_overrides"]["짜장면"]
gate = imgf["representative_gate_thresholds"]["짜장면"]
default = ovr.get("_default", {})

axis = "면컨디션"


def cfg(key, fallback):
    if key in ovr.get(axis, {}):
        return ovr[axis][key]
    if key in default:
        return default[key]
    return fallback


th = imgf.get("thresholds", {})
w_food = float(th.get("food_presence_weight", 1.0))
w_non = float(th.get("non_food_penalty_weight", 0.6))
w_cat = float(th.get("category_bonus_weight", 0.3))
w_wrong = float(th.get("wrong_food_weight", 0.7))

wrong_thr = float(gate.get("wrong_food_threshold", 0.31))
margin_thr = float(gate.get("wrong_food_margin_threshold", 0.04))
max_per_rest = int(gate.get("max_per_restaurant", 1))

w_clip = float(cfg("image_axis_weight", 2.8))
w_pres = float(cfg("axis_presence_weight", 1.6))
w_wv = float(cfg("axis_wrong_weight", 2.2))
max_wv = cfg("max_axis_wrong_visual", None)
min_pres = cfg("min_axis_presence", None)

desired = {4196102, 4196105, 4246502, 4248603, 4288400, 4289702}

print(f"=== 면컨디션 시뮬레이션 (new gate: wrong<{wrong_thr}, margin<{margin_thr}) ===")
print(f"new weights: clip={w_clip} presence={w_pres} wrong_visual={w_wv}")
print(f"axis gates: presence>={min_pres}, wrong_visual<={max_wv}\n")

# Walk through 면컨디션 records OR _excluded_ with desired ids
records = []
seen_for_desired = set()
for e in DEBUG.get("image_axis_evaluations", []):
    iid = int(e.get("image_id") or 0)
    ax = e.get("axis")

    if ax == "면컨디션":
        # rejected globally? no, those are _excluded_. So these passed gate.
        bd = e.get("score_breakdown") or {}
        records.append({
            "iid": iid,
            "rest": e.get("restaurant_id"),
            "wrong": e.get("wrong_food_penalty", 0),
            "cat": e.get("category_presence_score", 0),
            "food": e.get("food_presence_score", 0),
            "non": e.get("non_food_penalty", 0),
            "axis_clip": bd.get("image_axis_score", e.get("image_axis_clip_score", 0)),
            "presence": bd.get("axis_presence_score", 0),
            "wrong_visual": bd.get("axis_wrong_visual_score", 0),
            "in_desired": iid in desired,
            "src": "면컨디션-record",
        })
    elif ax == "_excluded_" and iid in desired:
        # the global gate cut it; we want to know if new gate passes
        if iid in seen_for_desired:
            continue
        seen_for_desired.add(iid)
        # No axis-level metrics here. Use rough estimates: presence from old (same image_id was evaluated in prior run). Actually we don't have that data directly.
        records.append({
            "iid": iid,
            "rest": e.get("restaurant_id"),
            "wrong": e.get("wrong_food_penalty", 0),
            "cat": e.get("category_presence_score", 0),
            "food": e.get("food_presence_score", 0),
            "non": e.get("non_food_penalty", 0),
            "axis_clip": None,
            "presence": None,
            "wrong_visual": None,
            "in_desired": True,
            "src": "(was-excluded)",
        })

# Compute new gate + new axis-level rejection + new score
def evaluate(r):
    new_gate = "PASS"
    if r["wrong"] > wrong_thr:
        new_gate = f"GATE_REJECT (wrong {r['wrong']:.3f}>{wrong_thr})"
    elif (r["wrong"] - r["cat"]) > margin_thr:
        new_gate = f"GATE_REJECT (margin {r['wrong']-r['cat']:+.3f}>{margin_thr})"

    axis_status = "?"
    score = None
    if new_gate == "PASS" and r["presence"] is not None:
        if min_pres is not None and r["presence"] < min_pres:
            axis_status = f"AXIS_REJECT_presence ({r['presence']:.3f}<{min_pres})"
        elif max_wv is not None and r["wrong_visual"] > max_wv:
            axis_status = f"AXIS_REJECT_wrong ({r['wrong_visual']:.3f}>{max_wv})"
        else:
            axis_status = "AXIS_OK"
            score = (w_clip * r["axis_clip"] + w_pres * r["presence"]
                     + w_cat * r["cat"] + w_food * r["food"]
                     - w_non * r["non"] - w_wrong * r["wrong"] - w_wv * r["wrong_visual"])
    return new_gate, axis_status, score


for r in records:
    g, a, s = evaluate(r)
    mark = "★ DESIRED" if r["in_desired"] else "  "
    score_str = f"{s:.3f}" if s is not None else "n/a"
    print(f"{mark}  id={r['iid']:>10}  rest={r['rest']:>4}  src={r['src']:<20}  gate={g:30}  axis={a:30}  score={score_str}")

# rank passing ones
print("\n=== Ranked (passing both gate + axis) ===")
ranked = [(r, *evaluate(r)) for r in records if r["presence"] is not None]
ranked = [(r, g, a, s) for r, g, a, s in ranked if g == "PASS" and a == "AXIS_OK"]
ranked.sort(key=lambda x: -(x[3] or -999))
seen_rest = set()
rank = 0
for r, g, a, s in ranked:
    if r["rest"] in seen_rest:
        continue
    seen_rest.add(r["rest"])
    rank += 1
    mark = "★" if r["in_desired"] else " "
    print(f"  #{rank} {mark} id={r['iid']:>10} rest={r['rest']:>4} score={s:.3f}")
    if rank >= 8:
        break
