"""기존 debug_multimodal_짜장면.json의 raw wrong/category 점수에 새 짜장면 gate를 적용해서
   문제의 짬뽕 사진(4337411 등)이 컷되는지 확인."""
import json

DEBUG = json.load(open("debug_multimodal_짜장면.json", encoding="utf-8"))
RULES = json.load(open("stand/stand_rules.json", encoding="utf-8"))
gate = RULES["image_filtering"]["representative_gate_thresholds"]["짜장면"]
wrong_thr = float(gate.get("wrong_food_threshold", gate.get("max_wrong_food_penalty", 0.2)))
margin_thr = gate.get("wrong_food_margin_threshold")
margin_thr = float(margin_thr) if margin_thr is not None else None

# Pull the 감칠맛 / 면컨디션 / 양많음 selected records and a few specific 짬뽕 ones
focus = [4337411, 4337419, 4337404,  # 삼선 (짬뽕 식당) 다수
         4177001,                     # 한원 — 면컨디션으로 뽑힌 것
         4115000, 4106200,            # 4324 식당 — 소스농도 뽑힌 것
         4252601, 4252800,            # 희객 4329
         4196102, 4196105, 4191503,   # desired
         4279301, 4305502, 4306100]   # desired 양많음

seen = set()
print(f"=== 짜장면 wrong gate: wrong_thr={wrong_thr}, margin_thr={margin_thr} ===\n")
print(f"{'image_id':>10}  {'rest':>5}  {'wrong':>6}  {'cat':>6}  margin   gate")
print("-" * 70)
for rec in DEBUG.get("image_axis_evaluations", []):
    iid = int(rec.get("image_id") or 0)
    if iid not in focus or iid in seen:
        continue
    seen.add(iid)
    bd = rec.get("score_breakdown") or {}
    wrong = bd.get("wrong_food_penalty", rec.get("wrong_food_penalty", 0))
    cat = bd.get("category_score", rec.get("category_presence_score", 0))
    margin = wrong - cat
    if wrong > wrong_thr:
        gate_result = f"REJECT (wrong {wrong:.3f} > {wrong_thr})"
    elif margin_thr is not None and margin > margin_thr:
        gate_result = f"REJECT (margin {margin:.3f} > {margin_thr})"
    else:
        gate_result = "PASS"
    rest = rec.get("restaurant_id")
    print(f"{iid:>10}  {rest:>5}  {wrong:6.3f}  {cat:6.3f}  {margin:+6.3f}   {gate_result}")
