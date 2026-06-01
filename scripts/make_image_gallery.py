import argparse
import html
import json
from datetime import datetime
from pathlib import Path


DEFAULT_TARGETS = {"\uce58\ud0a8", "\uccad\uad6d\uc7a5"}
ROOT = Path(__file__).resolve().parents[1]


def nz(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def short_text(value, limit=160):
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_representative_images(keyword):
    for path in sorted(ROOT.glob("*_representative_images.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        if data.get("keyword") == keyword:
            return path, data
    return None, {"images": []}


def collect_gallery_items(debug_data, rep_data):
    rep_by_id = {
        int(item.get("image_id")): item
        for item in rep_data.get("images", [])
        if item.get("image_id") is not None
    }
    items = {}

    for evaluation in debug_data.get("image_axis_evaluations", []):
        image_id = evaluation.get("image_id")
        if image_id is None:
            continue
        image_id = int(image_id)
        local_rel = f"image_cache/s3/{image_id}.jpg"
        record = items.setdefault(
            image_id,
            {
                "image_id": image_id,
                "src": local_rel,
                "local_exists": (ROOT / local_rel).exists(),
                "image_url": evaluation.get("image_url") or "",
                "review_id": evaluation.get("review_id"),
                "restaurant_id": evaluation.get("restaurant_id"),
                "restaurant": evaluation.get("restaurant_name") or "",
                "source_order": evaluation.get("source_order"),
                "selected": False,
                "rep_axis": "",
                "rep_score": None,
                "rep_review": "",
                "best_axis": "",
                "best_score": None,
                "passed_axis_count": 0,
                "food": nz(evaluation.get("food_presence_score")),
                "category": nz(evaluation.get("category_presence_score")),
                "wrong": nz(evaluation.get("wrong_food_penalty")),
                "non_food": nz(evaluation.get("non_food_penalty")),
                "reasons": {},
                "top_axes": [],
            },
        )

        record["food"] = max(record["food"], nz(evaluation.get("food_presence_score")))
        record["category"] = max(record["category"], nz(evaluation.get("category_presence_score")))
        record["wrong"] = max(record["wrong"], nz(evaluation.get("wrong_food_penalty")))
        record["non_food"] = max(record["non_food"], nz(evaluation.get("non_food_penalty")))
        if evaluation.get("selected"):
            record["selected"] = True

        reason = evaluation.get("excluded_reason") or "passed"
        record["reasons"][reason] = record["reasons"].get(reason, 0) + 1

        final_score = evaluation.get("final_score")
        if final_score is not None:
            record["passed_axis_count"] += 1
        record["top_axes"].append(
            {
                "axis": evaluation.get("axis_label") or evaluation.get("axis") or "",
                "score": round(nz(final_score, -999.0), 4) if final_score is not None else None,
                "clip": round(nz(evaluation.get("image_axis_clip_score")), 4),
                "category": round(nz(evaluation.get("category_presence_score")), 4),
                "wrong": round(nz(evaluation.get("wrong_food_penalty")), 4),
                "reason": reason,
            }
        )

    for image_id, rep_item in rep_by_id.items():
        record = items.get(image_id)
        if not record:
            continue
        record["selected"] = True
        record["rep_axis"] = rep_item.get("label") or rep_item.get("axis") or ""
        record["rep_score"] = rep_item.get("score")
        record["rep_review"] = short_text(rep_item.get("review_snippet") or "")

    for record in items.values():
        record["top_axes"].sort(
            key=lambda axis: -999 if axis["score"] is None else axis["score"],
            reverse=True,
        )
        record["top_axes"] = record["top_axes"][:5]
        if record["top_axes"]:
            record["best_axis"] = record["top_axes"][0]["axis"]
            record["best_score"] = record["top_axes"][0]["score"]
        record["reasons"] = dict(
            sorted(record["reasons"].items(), key=lambda item: (-item[1], item[0]))[:4]
        )

    return sorted(items.values(), key=lambda item: (not item["selected"], item["image_id"]))


def build_payload(keyword, debug_path, debug_data, rep_path, rep_data):
    cards = collect_gallery_items(debug_data, rep_data)
    axis_names = []
    seen = set()
    for axis in debug_data.get("representative_candidate_report", {}).get("axis_order") or []:
        if axis and axis not in seen:
            axis_names.append(axis)
            seen.add(axis)
    for card in cards:
        for axis in [card.get("best_axis"), card.get("rep_axis")]:
            if axis and axis not in seen:
                axis_names.append(axis)
                seen.add(axis)

    return {
        "keyword": keyword,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "debug_path": debug_path.relative_to(ROOT).as_posix(),
        "rep_path": rep_path.relative_to(ROOT).as_posix() if rep_path else "",
        "total": len(cards),
        "selected_count": sum(1 for item in cards if item["selected"]),
        "axis_names": axis_names,
        "restaurants": sorted({item["restaurant"] for item in cards if item.get("restaurant")}),
        "items": cards,
    }


def render_html(payload):
    json_payload = json.dumps(payload, ensure_ascii=False)
    keyword = payload["keyword"]
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(keyword)} JPG Gallery</title>
<style>
:root {{ color-scheme: light; --ink:#171717; --muted:#666; --line:#e8e4dc; --paper:#fff; --bg:#f7f5f0; --accent:#f26b52; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, 'Malgun Gothic', sans-serif; background: var(--bg); color: var(--ink); }}
header {{ position: sticky; top: 0; z-index: 3; background: rgba(247,245,240,.94); backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); padding: 16px 22px 14px; }}
h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
.sub {{ color: var(--muted); font-size: 13px; display:flex; gap:12px; flex-wrap:wrap; }}
.controls {{ display: grid; grid-template-columns: minmax(220px, 1fr) 170px 170px 140px 140px; gap: 10px; margin-top: 14px; }}
input, select {{ width: 100%; height: 38px; border: 1px solid var(--line); border-radius: 7px; background: white; padding: 0 10px; font-size: 14px; }}
main {{ padding: 20px 22px 80px; }}
.status {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 14px; color: var(--muted); font-size: 13px; }}
.pager {{ display:flex; gap:8px; align-items:center; }}
button {{ border:1px solid var(--line); background:white; color:var(--ink); border-radius:7px; min-height:34px; padding:0 11px; cursor:pointer; font-weight:700; }}
button:disabled {{ opacity:.35; cursor:default; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 14px; align-items:start; }}
.card {{ background: var(--paper); border:1px solid var(--line); border-radius:8px; overflow:hidden; box-shadow: 0 7px 20px rgba(0,0,0,.06); }}
.card.selected {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(242,107,82,.18), 0 10px 24px rgba(0,0,0,.08); }}
.thumb {{ width:100%; aspect-ratio: 1 / 1; display:block; object-fit:cover; background:#eee; }}
.body {{ padding: 9px 10px 10px; }}
.idrow {{ display:flex; justify-content:space-between; gap:8px; align-items:center; font-size: 13px; font-weight:800; }}
.badge {{ display:inline-flex; align-items:center; min-height:20px; border-radius:999px; padding:2px 7px; font-size:11px; background:#f3eee7; color:#4b4138; white-space:nowrap; }}
.badge.sel {{ background:#fff0ec; color:#c43f28; }}
.meta {{ margin-top:6px; color:var(--muted); font-size:11px; line-height:1.45; word-break:break-word; }}
.chips {{ display:flex; gap:5px; flex-wrap:wrap; margin-top:7px; }}
.chip {{ border:1px solid #eee3d7; background:#fbf8f3; border-radius:999px; padding:2px 6px; font-size:11px; line-height:1.3; }}
.reason {{ margin-top:7px; color:#8a5b46; font-size:10px; line-height:1.35; }}
.actions {{ display:flex; gap:6px; margin-top:8px; }}
.actions button {{ min-height:28px; font-size:11px; padding:0 8px; font-weight:700; }}
.empty {{ padding:40px; text-align:center; color:var(--muted); background:white; border:1px solid var(--line); border-radius:8px; }}
@media (max-width: 860px) {{ .controls {{ grid-template-columns: 1fr 1fr; }} h1 {{ font-size:24px; }} }}
@media (max-width: 520px) {{ header, main {{ padding-left:14px; padding-right:14px; }} .controls {{ grid-template-columns: 1fr; }} .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
</style>
</head>
<body>
<header>
  <h1><span id="kw"></span> JPG Gallery</h1>
  <div class="sub"><span id="countLabel"></span><span id="sourceLabel"></span><span id="timeLabel"></span></div>
  <div class="controls">
    <input id="search" placeholder="image_id, restaurant, review_id search" />
    <select id="axisFilter"><option value="">all axes</option></select>
    <select id="restaurantFilter"><option value="">all restaurants</option></select>
    <select id="modeFilter"><option value="">all images</option><option value="selected">selected</option><option value="passed">has passed axis</option><option value="rejected">only rejected</option><option value="missing">missing local file</option></select>
    <select id="pageSize"><option>120</option><option>240</option><option>480</option><option value="99999">all</option></select>
  </div>
</header>
<main>
  <div class="status"><div id="resultLabel"></div><div class="pager"><button id="prevBtn">Prev</button><span id="pageLabel"></span><button id="nextBtn">Next</button></div></div>
  <section id="grid" class="grid"></section>
</main>
<script id="galleryData" type="application/json">{json_payload}</script>
<script>
const DATA = JSON.parse(document.getElementById('galleryData').textContent);
const state = {{ page: 1 }};
const $ = (id) => document.getElementById(id);
$('kw').textContent = DATA.keyword;
$('countLabel').textContent = `${{DATA.total}} local JPG candidates / selected ${{DATA.selected_count}}`;
$('sourceLabel').textContent = DATA.debug_path;
$('timeLabel').textContent = DATA.generated_at;
for (const axis of DATA.axis_names) {{ const opt = document.createElement('option'); opt.value = axis; opt.textContent = axis; $('axisFilter').appendChild(opt); }}
for (const name of DATA.restaurants) {{ const opt = document.createElement('option'); opt.value = name; opt.textContent = name; $('restaurantFilter').appendChild(opt); }}
function fmt(v) {{ return v === null || v === undefined ? '-' : Number(v).toFixed(3); }}
function escapeHtml(s) {{ return String(s ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch])); }}
function copyText(text) {{ navigator.clipboard?.writeText(String(text)).catch(() => prompt('copy', text)); }}
function filtered() {{
  const q = $('search').value.trim().toLowerCase(); const axis = $('axisFilter').value; const rest = $('restaurantFilter').value; const mode = $('modeFilter').value;
  return DATA.items.filter(item => {{
    if (q) {{ const hay = `${{item.image_id}} ${{item.review_id}} ${{item.restaurant_id}} ${{item.restaurant}} ${{item.best_axis}} ${{item.rep_axis}}`.toLowerCase(); if (!hay.includes(q)) return false; }}
    if (axis && item.best_axis !== axis && item.rep_axis !== axis && !item.top_axes.some(a => a.axis === axis)) return false;
    if (rest && item.restaurant !== rest) return false;
    if (mode === 'selected' && !item.selected) return false;
    if (mode === 'passed' && item.passed_axis_count <= 0) return false;
    if (mode === 'rejected' && item.passed_axis_count > 0) return false;
    if (mode === 'missing' && item.local_exists) return false;
    return true;
  }});
}}
function render() {{
  const rows = filtered(); const size = Number($('pageSize').value); const pages = Math.max(1, Math.ceil(rows.length / size)); state.page = Math.min(Math.max(1, state.page), pages);
  const start = (state.page - 1) * size; const slice = rows.slice(start, start + size);
  $('resultLabel').textContent = `${{rows.length}} matched, showing ${{slice.length}}`; $('pageLabel').textContent = `${{state.page}} / ${{pages}}`; $('prevBtn').disabled = state.page <= 1; $('nextBtn').disabled = state.page >= pages;
  $('grid').innerHTML = slice.length ? slice.map(item => {{
    const axes = item.top_axes.map(a => `<span class="chip">${{escapeHtml(a.axis)}} ${{a.score === null ? '' : fmt(a.score)}}</span>`).join('');
    const reasons = Object.entries(item.reasons || {{}}).map(([k,v]) => `${{escapeHtml(k)}}:${{v}}`).join(' / ');
    return `<article class="card ${{item.selected ? 'selected' : ''}}"><a href="${{escapeHtml(item.src)}}" target="_blank"><img class="thumb" src="${{escapeHtml(item.src)}}" loading="lazy" alt="${{item.image_id}}" /></a><div class="body"><div class="idrow"><span>#${{item.image_id}}</span><span class="badge ${{item.selected ? 'sel' : ''}}">${{item.selected ? 'selected' : (item.passed_axis_count ? 'passed' : 'rejected')}}</span></div><div class="meta">${{escapeHtml(item.restaurant || '-')}}<br/>review ${{item.review_id ?? '-'}} / rest ${{item.restaurant_id ?? '-'}}<br/>best: <b>${{escapeHtml(item.best_axis || '-')}}</b> ${{fmt(item.best_score)}}<br/>food ${{fmt(item.food)}} / cat ${{fmt(item.category)}} / wrong ${{fmt(item.wrong)}}</div>${{item.rep_axis ? `<div class="meta"><b>rep</b>: ${{escapeHtml(item.rep_axis)}} ${{fmt(item.rep_score)}}<br/>${{escapeHtml(item.rep_review)}}</div>` : ''}}<div class="chips">${{axes}}</div><div class="reason">${{reasons}}</div><div class="actions"><button onclick="copyText('${{item.image_id}}')">copy id</button><button onclick="copyText('${{escapeHtml(item.src)}}')">copy path</button><button onclick="copyText('${{escapeHtml(item.image_url)}}')">copy url</button></div></div></article>`;
  }}).join('') : '<div class="empty">No images matched.</div>';
}}
for (const id of ['search','axisFilter','restaurantFilter','modeFilter','pageSize']) {{ $(id).addEventListener('input', () => {{ state.page = 1; render(); }}); }}
$('prevBtn').addEventListener('click', () => {{ state.page--; render(); window.scrollTo({{top:0, behavior:'smooth'}}); }});
$('nextBtn').addEventListener('click', () => {{ state.page++; render(); window.scrollTo({{top:0, behavior:'smooth'}}); }});
render();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keywords", nargs="*")
    args = parser.parse_args()
    targets = set(args.keywords) if args.keywords else DEFAULT_TARGETS
    results = []

    for debug_path in sorted(ROOT.glob("debug_multimodal_*.json")):
        debug_data = load_json(debug_path)
        keyword = debug_data.get("keyword")
        if keyword not in targets:
            continue
        rep_path, rep_data = find_representative_images(keyword)
        payload = build_payload(keyword, debug_path, debug_data, rep_path, rep_data)
        output_path = ROOT / f"gallery_{keyword}.html"
        output_path.write_text(render_html(payload), encoding="utf-8")
        results.append((output_path, payload["total"], payload["selected_count"]))

    missing = targets - {path.stem.removeprefix("gallery_") for path, _, _ in results}
    if missing:
        raise SystemExit(f"missing debug files for: {sorted(missing)}")
    for path, total, selected in results:
        print(f"{path.name}: items={total}, selected={selected}")


if __name__ == "__main__":
    main()
