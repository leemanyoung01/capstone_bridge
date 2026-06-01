"""건더기양 새 가중치로 시뮬레이션. (raw axis_clip/presence/wrong_visual은 다음 EC2 재측정 때 바뀌니 추정)"""
import json

DEBUG = json.load(open("debug_multimodal_청국장.json", encoding="utf-8"))

# new weights (matching current stand_rules.json)
W_CLIP = 3.0
W_PRES = 6.2
W_WV = 3.4
MIN_PRES = 0.24
MAX_WV = 0.34
W_FOOD = 1.0
W_NON = 0.6
W_CAT = 0.3
W_WRONG = 0.7

# new max_per_restaurant = 2
# new wrong gate (global): wrong_food_threshold = 0.30, margin = 0.04
GATE_WRONG = 0.30
GATE_MARGIN = 0.04

rows = []
for e in DEBUG.get("image_axis_evaluations", []):
    if e.get("axis") != "건더기양":
        continue
    bd = e.get("score_breakdown") or {}
    cli = bd.get("image_axis_score", 0)
    pres = bd.get("axis_presence_score", 0)
    wv = bd.get("axis_wrong_visual_score", 0)
    cat = bd.get("category_score", 0)
    food = bd.get("food_presence_score", 0)
    non = bd.get("non_food_penalty", 0)
    wrong = bd.get("wrong_food_penalty", 0)
    # filter axis-level (presence/wrong_visual)
    if pres < MIN_PRES:
        continue
    if wv > MAX_WV:
        continue
    new = (W_CLIP*cli + W_PRES*pres + W_CAT*cat + W_FOOD*food
           - W_NON*non - W_WRONG*wrong - W_WV*wv)
    rows.append({
        "iid": int(e.get("image_id") or 0),
        "rest": e.get("restaurant_id"),
        "rname": (e.get("restaurant_name") or "")[:14],
        "new_score": new,
        "old_score": bd.get("final_score", 0),
        "cli": cli, "pres": pres, "wv": wv, "wrong": wrong, "cat": cat,
    })

rows.sort(key=lambda x: -x["new_score"])
desired = {2119002, 2123203}
print("=== 건더기양 NEW ranking (with new w_wv=3.4, min_pres=0.24, agg=max — TOP 15) ===\n")
print(f"{'rank':>4} mark {'iid':>10} {'rest':>5}({'rname':<14}) {'pres':>5} {'wv':>5} {'cli':>5}   old→new")
for i, r in enumerate(rows[:15], 1):
    mark = "★" if r["iid"] in desired else " "
    print(f"{i:>4} {mark}   {r['iid']:>10}  {r['rest']:>4}({r['rname']:<14}) {r['pres']:.3f} {r['wv']:.3f} {r['cli']:.3f}   {r['old_score']:.2f}→{r['new_score']:.2f}")

print("\n=== Desired absolute positions ===")
for r in rows:
    if r["iid"] in desired:
        pos = rows.index(r) + 1
        print(f"  #{pos}  id={r['iid']}  rest={r['rest']}  new_score={r['new_score']:.3f}")
