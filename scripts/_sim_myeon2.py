"""이번 debug raw 값에 새 면컨디션 weight를 대입.
   주의: lexicon clip_prompt_pos를 좁혔으니 axis_clip raw 값도 다시 측정해야 정확.
   여기선 *기존 axis_clip 값*에 새 가중치만 곱한 추정. presence/wrong_visual은 그대로."""
import json

DEBUG = json.load(open("debug_multimodal_짜장면.json", encoding="utf-8"))
RULES = json.load(open("stand/stand_rules.json", encoding="utf-8"))

# new weights
W_CLIP = 3.0
W_PRES = 9.5
W_WV = 3.6

th = RULES["image_filtering"].get("thresholds", {})
w_food = float(th.get("food_presence_weight", 1.0))
w_non = float(th.get("non_food_penalty_weight", 0.6))
w_cat = float(th.get("category_bonus_weight", 0.3))
w_wrong = float(th.get("wrong_food_weight", 0.7))

axis_rows = []
for e in DEBUG.get("image_axis_evaluations", []):
    if e.get("axis") != "면컨디션":
        continue
    if e.get("excluded_reason"):
        continue
    bd = e.get("score_breakdown") or {}
    axis_clip = bd.get("image_axis_score", 0)
    pres = bd.get("axis_presence_score", 0)
    wv = bd.get("axis_wrong_visual_score", 0)
    cat = bd.get("category_score", 0)
    food = bd.get("food_presence_score", 0)
    non = bd.get("non_food_penalty", 0)
    wrong = bd.get("wrong_food_penalty", 0)
    new_score = (W_CLIP*axis_clip + W_PRES*pres + w_cat*cat + w_food*food
                 - w_non*non - w_wrong*wrong - W_WV*wv)
    axis_rows.append({
        "iid": int(e.get("image_id") or 0),
        "rest": e.get("restaurant_id"),
        "rname": (e.get("restaurant_name") or "")[:12],
        "axis_clip": axis_clip,
        "presence": pres,
        "wrong_v": wv,
        "old_score": bd.get("final_score", 0),
        "new_score": new_score,
    })

desired = {4196102, 4196105, 4246502, 4248603, 4288400, 4289702}

axis_rows.sort(key=lambda x: -x["new_score"])

print(f"=== 면컨디션 NEW weights (clip=3.0, presence=9.5) — top 15 by NEW score ===")
print(f"{'rank':>4}  {'mark':<3}  {'iid':>10}  {'rest':>5}({'rname':<12}) clip pres wrong_v   old→new")
seen_rest = set()
rank = 0
shown = 0
for r in axis_rows:
    if r["rest"] in seen_rest:
        continue
    seen_rest.add(r["rest"])
    rank += 1
    mark = "★" if r["iid"] in desired else " "
    print(f"{rank:>4}  {mark:<3}  {r['iid']:>10}  {r['rest']:>5}({r['rname']:<12}) {r['axis_clip']:.3f} {r['presence']:.3f} {r['wrong_v']:.3f}   {r['old_score']:.2f}→{r['new_score']:.2f}")
    shown += 1
    if shown >= 12:
        break

print(f"\n=== Desired images NEW ranks (across all candidates, not just per-restaurant) ===")
all_sorted = sorted(axis_rows, key=lambda x: -x["new_score"])
for r in all_sorted:
    if r["iid"] in desired:
        rk = all_sorted.index(r) + 1
        print(f"  rank #{rk}  id={r['iid']}  rest={r['rest']}  presence={r['presence']:.3f}  old={r['old_score']:.2f}  new={r['new_score']:.2f}")
