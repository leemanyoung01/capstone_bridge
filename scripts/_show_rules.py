import json

data = json.load(open("stand/stand_rules.json", encoding="utf-8"))
imgf = data["image_filtering"]

print("=== thresholds (global) ===")
print(json.dumps(imgf["thresholds"], ensure_ascii=False, indent=2))

print("\n=== representative_gate_thresholds['짜장면'] ===")
print(json.dumps(imgf["representative_gate_thresholds"]["짜장면"], ensure_ascii=False, indent=2))

print("\n=== axis_score_overrides['짜장면'] (per-axis weights only) ===")
ovr = imgf["axis_score_overrides"]["짜장면"]
for axis, cfg in ovr.items():
    print(f"--- {axis} ---")
    for k in (
        "image_axis_weight",
        "axis_presence_weight",
        "axis_wrong_weight",
        "linked_text_weight",
        "min_axis_presence",
        "max_axis_wrong_visual",
        "positive_prompt_limit",
        "negative_prompt_limit",
        "axis_presence_aggregation",
        "axis_presence_top_k",
        "axis_wrong_aggregation",
        "axis_wrong_top_k",
    ):
        if k in cfg:
            print(f"  {k}: {cfg[k]}")
