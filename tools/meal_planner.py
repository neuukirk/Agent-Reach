#!/usr/bin/env python3
"""Meal planner + shopping-list generator for the saved Instagram recipes.

Reads ``recipes/index.yaml`` for metadata and the recipe markdown files for
ingredients, then builds a weekly plan and a single consolidated, aisle-grouped
shopping list. Cost-optimized by default; toggle other modes with ``--mode``.

Each recipe links back to its original Instagram reel, so the plan doubles as a
set of how-to videos (see the "📹" line per recipe).

Usage:
  python tools/meal_planner.py plan [--days N] [--mode budget|variety|quick]
                                    [--shuffle] [--seed N]
                                    [--pin SLUG ...] [--exclude SLUG ...]
                                    [--no-shopping-list]
  python tools/meal_planner.py list [--sort cost|time|cuisine]
  python tools/meal_planner.py shopping SLUG [SLUG ...]
  python tools/meal_planner.py readme            # regenerate recipes/README.md

SLUG = recipe filename without .md (e.g. marry-me-tortellini).
"""
from __future__ import annotations

import argparse
import html
import random
import re
import sys
from pathlib import Path

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_DIR / "recipes"
INDEX_FILE = RECIPES_DIR / "index.yaml"
SHOPPING_DIR = REPO_DIR / "shopping"
STAPLES_FILE = SHOPPING_DIR / "staples.yaml"

# Aisle routing. First rule whose keyword is a substring of the (lowercased)
# ingredient line wins, so order matters: compound exceptions come before the
# generic words they contain (e.g. "chicken broth" -> Pantry, not Meat).
AISLE_RULES: list[tuple[str, list[str]]] = [
    ("Pantry & Canned", ["broth", "stock"]),
    ("Pantry & Canned", ["soy sauce", "fish sauce", "hot sauce", "buffalo sauce",
                          "chili sauce", "chilli sauce", "tomato sauce", "salsa",
                          "worcestershire"]),
    ("Pantry & Canned", ["garlic powder", "garlic paste", "garlic salt",
                          "onion powder", "ginger paste", "tomato paste"]),
    ("Dairy & Eggs", ["cream cheese", "sour cream", "cottage cheese",
                       "creme fraiche", "crème fraîche", "heavy cream",
                       "evaporated milk", "buttermilk", "greek yogurt", "yogurt",
                       "yoghurt", "mozzarella", "cheddar", "parmesan",
                       "parmigiano", "gruyere", "gruyère", "blue cheese",
                       "cheese", "butter", "milk", "cream", "egg"]),
    ("Meat & Seafood", ["chicken", "beef", "pork", "sausage", "bacon",
                         "sirloin", "mince", "ground"]),
    ("Frozen", ["pizza roll", "frozen", "tostada"]),
    ("Household", ["paper towel", "dish soap", "trash bag", "detergent",
                   "napkin", "foil", "sponge", "toilet", "shampoo", "cleaner",
                   "ziploc", "plastic wrap", "tissue", "bin bag"]),
    ("Produce", ["onion", "garlic", "ginger", "tomato", "lettuce", "cucumber",
                 "jalap", "bell pepper", "potato", "carrot", "celery", "spinach",
                 "coriander", "cilantro", "parsley", "scallion", "spring onion",
                 "green onion", "lemon", "lime", "orange", "chive", "basil",
                 "thyme", "avocado", "mushroom", "rosemary", "fresno", "dill",
                 "banana", "berry", "berries", "apple", "lettuce", "kale"]),
    ("Pantry & Canned", ["flour", "cornstarch", "corn starch", "sugar", "rice",
                          "noodle", "pasta", "spaghetti", "tortellini",
                          "macaroni", "breadcrumb", "panko", "cornflake",
                          "corn flake", "soy", "sriracha", "gochujang",
                          "gochugaru", "mayo", "mayonnaise", "ranch",
                          "guacamole", "baking", "salt", "pepper", "paprika",
                          "cumin", "turmeric", "masala", "chili", "chilli",
                          "sesame", "peanut butter", "cashew", "pickle",
                          "sun-dried", "sundried", "sun dried", "wrapper",
                          "biscuit", "tortilla", "ciabatta", "sourdough",
                          "bread", "honey", "syrup", "vinegar", "oil", "wine",
                          "seasoning", "sauce", "paste", "nutmeg", "pea",
                          "coffee", "tea", "cereal", "oat", "honey", "jam"]),
]

AISLE_ORDER = ["Produce", "Meat & Seafood", "Dairy & Eggs", "Pantry & Canned",
               "Frozen", "Household", "Other"]

IG_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+")


def load_recipes() -> list[dict]:
    if not INDEX_FILE.exists():
        sys.exit(f"Missing manifest: {INDEX_FILE}")
    data = yaml.safe_load(INDEX_FILE.read_text())
    recipes = data.get("recipes", [])
    for r in recipes:
        r["slug"] = r["file"][:-3] if r["file"].endswith(".md") else r["file"]
        servings = max(1, int(r.get("servings", 1)))
        r["cost_per_serving"] = round(float(r.get("est_cost_usd", 0)) / servings, 2)
    return recipes


def by_slug(recipes: list[dict]) -> dict[str, dict]:
    return {r["slug"]: r for r in recipes}


def recipe_text(slug: str) -> str:
    path = RECIPES_DIR / f"{slug}.md"
    return path.read_text() if path.exists() else ""


def video_link(slug: str) -> str | None:
    m = IG_URL_RE.search(recipe_text(slug))
    return m.group(0).rstrip(").") if m else None


def parse_ingredients(slug: str) -> list[str]:
    """Pull the bulleted lines from the '## Ingredients' section of a recipe."""
    text = recipe_text(slug)
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip().lower().startswith("ingredient")
            continue
        if in_section and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item:
                out.append(item)
    return out


def parse_steps(slug: str) -> list[str]:
    """Pull numbered steps from the '## Instructions' section of a recipe."""
    text = recipe_text(slug)
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip().lower().startswith("instruction")
            continue
        if in_section:
            m = re.match(r"^\d+\.\s+(.*)", stripped)
            if m:
                # Strip markdown bold and a leading "Label:" prefix.
                step = m.group(1).replace("**", "")
                out.append(step.strip())
    return out


def reel_embed_url(slug: str) -> str | None:
    url = video_link(slug)
    if not url:
        return None
    m = re.search(r"/reel/([A-Za-z0-9_-]+)", url)
    return f"https://www.instagram.com/reel/{m.group(1)}/embed" if m else None


def recipe_title(slug: str) -> str:
    text = recipe_text(slug)
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return slug


def generate_video_html(slug: str) -> str:
    """Build a self-contained HTML 'how-to video': the original reel embedded
    plus an auto-advancing, narrated-style slideshow of the recipe steps."""
    title = recipe_title(slug)
    steps = parse_steps(slug)
    ingredients = parse_ingredients(slug)
    embed = reel_embed_url(slug)

    # Per-step dwell time scales with reading length (min 5s).
    def dwell(step: str) -> int:
        return max(5, min(20, round(len(step.split()) / 2.5)))

    steps_json = ",\n".join(
        f'    {{"text": {_js(s)}, "secs": {dwell(s)}}}' for s in steps)
    ingredients_html = "".join(
        f"<li>{html.escape(i)}</li>" for i in ingredients)
    reel_block = (
        f'<iframe class="reel" src="{embed}" frameborder="0" '
        f'scrolling="no" allowtransparency="true"></iframe>'
        if embed else
        '<p class="noreel">No original reel available for this recipe.</p>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — How-To</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, system-ui, sans-serif;
         background: #14110f; color: #f4efe9; }}
  header {{ padding: 20px 24px; background: #1e1a16; border-bottom: 1px solid #2c2722; }}
  h1 {{ margin: 0; font-size: 22px; }}
  .wrap {{ display: flex; flex-wrap: wrap; gap: 24px; padding: 24px; }}
  .col {{ flex: 1 1 340px; min-width: 300px; }}
  .reel {{ width: 100%; height: 640px; border-radius: 14px; background: #000; }}
  .noreel {{ color: #998f82; }}
  .stage {{ background: #1e1a16; border: 1px solid #2c2722; border-radius: 14px;
            padding: 28px; min-height: 260px; display: flex; flex-direction: column; }}
  .counter {{ color: #d98a3d; font-weight: 600; letter-spacing: .04em; font-size: 13px; }}
  .step {{ font-size: 26px; line-height: 1.45; margin: 16px 0 auto; }}
  .bar {{ height: 4px; background: #2c2722; border-radius: 2px; margin-top: 20px; overflow: hidden; }}
  .fill {{ height: 100%; width: 0; background: #d98a3d; transition: width .25s linear; }}
  .controls {{ display: flex; gap: 10px; margin-top: 18px; }}
  button {{ flex: 1; padding: 12px; font-size: 15px; border: 0; border-radius: 9px;
            background: #2c2722; color: #f4efe9; cursor: pointer; }}
  button.primary {{ background: #d98a3d; color: #14110f; font-weight: 700; }}
  button:hover {{ filter: brightness(1.12); }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: .05em; color: #998f82; }}
  ul {{ padding-left: 20px; line-height: 1.7; }}
</style>
</head>
<body>
<header><h1>🍳 {html.escape(title)}</h1></header>
<div class="wrap">
  <div class="col">{reel_block}</div>
  <div class="col">
    <div class="stage">
      <div class="counter" id="counter"></div>
      <div class="step" id="step"></div>
      <div class="bar"><div class="fill" id="fill"></div></div>
      <div class="controls">
        <button id="prev">‹ Prev</button>
        <button id="play" class="primary">▶ Play</button>
        <button id="next">Next ›</button>
      </div>
    </div>
    <h2>Ingredients</h2>
    <ul>{ingredients_html}</ul>
  </div>
</div>
<script>
const steps = [
{steps_json}
];
let i = 0, playing = false, timer = null, t0 = 0, raf = null;
const stepEl = document.getElementById('step');
const counterEl = document.getElementById('counter');
const fillEl = document.getElementById('fill');
const playBtn = document.getElementById('play');
function render() {{
  stepEl.textContent = steps[i].text;
  counterEl.textContent = 'STEP ' + (i + 1) + ' / ' + steps.length;
  fillEl.style.width = '0%';
}}
function tickBar() {{
  const pct = Math.min(100, ((Date.now() - t0) / (steps[i].secs * 1000)) * 100);
  fillEl.style.width = pct + '%';
  if (playing) raf = requestAnimationFrame(tickBar);
}}
function schedule() {{
  clearTimeout(timer); cancelAnimationFrame(raf);
  t0 = Date.now(); tickBar();
  timer = setTimeout(() => {{
    if (i < steps.length - 1) {{ i++; render(); schedule(); }}
    else {{ stop(); }}
  }}, steps[i].secs * 1000);
}}
function play() {{ playing = true; playBtn.textContent = '⏸ Pause'; schedule(); }}
function stop() {{ playing = false; playBtn.textContent = '▶ Play';
  clearTimeout(timer); cancelAnimationFrame(raf); }}
playBtn.onclick = () => playing ? stop() : play();
document.getElementById('next').onclick = () => {{
  if (i < steps.length - 1) {{ i++; render(); if (playing) schedule(); }} }};
document.getElementById('prev').onclick = () => {{
  if (i > 0) {{ i--; render(); if (playing) schedule(); }} }};
render();
</script>
</body>
</html>
"""


def _js(s: str) -> str:
    """Encode a Python string as a safe JS/JSON string literal."""
    import json
    return json.dumps(s)


def generate_cookbook_html(recipes: list[dict]) -> str:
    """Build the single-page Cookbook app: planner + shopping assistant +
    recipe browser, all driven client-side from embedded recipe data."""
    import json
    data = {
        "aisleOrder": AISLE_ORDER,
        "staples": load_staples(),
        "recipes": [],
    }
    for r in recipes:
        data["recipes"].append({
            "slug": r["slug"], "title": r["title"], "cuisine": r["cuisine"],
            "category": r.get("category", "main"), "time": r["time_min"],
            "servings": r["servings"], "cost": r["est_cost_usd"],
            "cps": r["cost_per_serving"], "tags": r.get("tags", []),
            "video": video_link(r["slug"]),
            "embed": reel_embed_url(r["slug"]),
            "steps": parse_steps(r["slug"]),
            "ingredients": [{"t": it, "a": aisle_for(it)}
                            for it in parse_ingredients(r["slug"])],
        })
    return COOKBOOK_TEMPLATE.replace("__DATA__", json.dumps(data))


COOKBOOK_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🍳 Cookbook</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, system-ui, sans-serif;
         background: #14110f; color: #f4efe9; }
  header { padding: 18px 24px; background: #1e1a16; border-bottom: 1px solid #2c2722;
           display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { margin: 0; font-size: 21px; }
  .tabs { display: flex; gap: 6px; margin-left: auto; }
  .tab { padding: 8px 16px; border-radius: 8px; background: #2c2722; cursor: pointer;
         font-size: 14px; border: 0; color: #cfc6ba; }
  .tab.active { background: #d98a3d; color: #14110f; font-weight: 700; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .panel { display: none; } .panel.active { display: block; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 20px; }
  select, button { padding: 10px 14px; font-size: 14px; border: 0; border-radius: 9px;
                   background: #2c2722; color: #f4efe9; cursor: pointer; }
  button.primary { background: #d98a3d; color: #14110f; font-weight: 700; }
  button:hover { filter: brightness(1.12); }
  label.inline { font-size: 14px; color: #cfc6ba; display: flex; align-items: center; gap: 6px; }
  .card { background: #1e1a16; border: 1px solid #2c2722; border-radius: 12px;
          padding: 16px 18px; margin-bottom: 12px; }
  .card .day { color: #d98a3d; font-weight: 700; font-size: 13px; letter-spacing: .05em; }
  .card .meta { color: #998f82; font-size: 13px; margin-top: 4px; }
  .card a { color: #e6a85c; text-decoration: none; }
  .summary { font-size: 15px; color: #cfc6ba; margin: 8px 0 20px; }
  .aisle { margin-bottom: 18px; }
  .aisle h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
              color: #998f82; margin: 0 0 8px; }
  .chk { display: flex; align-items: center; gap: 9px; padding: 4px 0; font-size: 15px; }
  .cols { columns: 2; } @media (max-width: 640px) { .cols { columns: 1; } }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
  .recipe-row { display: flex; justify-content: space-between; gap: 10px;
                padding: 11px 14px; }
  .muted { color: #998f82; font-size: 13px; }
  textarea { width: 100%; height: 160px; background: #14110f; color: #cfc6ba;
             border: 1px solid #2c2722; border-radius: 9px; padding: 12px; font: inherit; }
  .modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.72);
           z-index: 50; padding: 24px; overflow: auto; }
  .modal.open { display: block; }
  .modal-box { max-width: 920px; margin: 24px auto; background: #1e1a16;
               border: 1px solid #2c2722; border-radius: 14px; padding: 22px; position: relative; }
  .modal-x { position: absolute; top: 14px; right: 14px; background: #2c2722;
             border-radius: 8px; padding: 6px 11px; }
  .modal-grid { display: grid; grid-template-columns: 320px 1fr; gap: 20px; margin-top: 12px; }
  @media (max-width: 720px) { .modal-grid { grid-template-columns: 1fr; } }
  .modal-grid iframe { width: 100%; height: 560px; border: 0; border-radius: 12px; background: #000; }
  .stage { background: #14110f; border: 1px solid #2c2722; border-radius: 12px;
           padding: 22px; min-height: 240px; display: flex; flex-direction: column; }
  .counter { color: #d98a3d; font-weight: 600; font-size: 13px; letter-spacing: .04em; }
  .mstep { font-size: 22px; line-height: 1.45; margin: 14px 0 auto; }
  .bar { height: 4px; background: #2c2722; border-radius: 2px; margin-top: 18px; overflow: hidden; }
  .fill { height: 100%; width: 0; background: #d98a3d; }
</style>
</head>
<body>
<header>
  <h1>🍳 Cookbook</h1>
  <div class="tabs">
    <button class="tab active" data-tab="plan">📅 Planner</button>
    <button class="tab" data-tab="shop">🛒 Shopping</button>
    <button class="tab" data-tab="browse">📖 Recipes</button>
  </div>
</header>
<main>
  <section class="panel active" id="plan">
    <div class="controls">
      <label class="inline">Mode
        <select id="mode">
          <option value="budget">Budget (cheapest)</option>
          <option value="variety">Variety (mixed cuisines)</option>
          <option value="quick">Quick (fastest)</option>
        </select>
      </label>
      <label class="inline">Dinners
        <select id="days">
          <option>3</option><option selected>5</option><option>7</option>
        </select>
      </label>
      <button class="primary" id="genBtn">Generate week</button>
      <button id="shufBtn">🔀 Shuffle</button>
    </div>
    <div class="summary" id="planSummary"></div>
    <div id="planList"></div>
  </section>

  <section class="panel" id="shop">
    <div class="controls">
      <label class="inline"><input type="checkbox" id="incPlan" checked> Include this week's plan ingredients</label>
      <button class="primary" id="buildBtn">Build shopping list</button>
      <button id="resetBtn">Reset to usuals</button>
    </div>
    <p class="muted">Check off the usuals you need this week, then build your list.</p>
    <div id="staples"></div>
    <h3 style="margin-top:28px">Your list</h3>
    <div id="shopOut"></div>
  </section>

  <section class="panel" id="browse">
    <div class="controls"><label class="inline">Sort
      <select id="sort">
        <option value="cps">Cost</option><option value="time">Time</option>
        <option value="cuisine">Cuisine</option>
      </select></label>
    </div>
    <div id="browseList"></div>
  </section>

  <div id="modal" class="modal">
    <div class="modal-box">
      <button class="modal-x" id="mClose">✕</button>
      <h2 id="mTitle" style="color:#f4efe9"></h2>
      <div class="modal-grid">
        <div id="mReel"></div>
        <div>
          <div class="stage">
            <div class="counter" id="mCounter"></div>
            <div class="mstep" id="mStep"></div>
            <div class="bar"><div class="fill" id="mFill"></div></div>
            <div class="controls">
              <button id="mPrev">‹ Prev</button>
              <button class="primary" id="mPlay">▶ Play</button>
              <button id="mNext">Next ›</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</main>
<script>
const DATA = __DATA__;
let currentPlan = [];

// --- tabs ---
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById(t.dataset.tab).classList.add('active');
});

// --- planner ---
function weight(r, mode){ return mode === 'quick' ? 1/Math.max(5, r.time) : 1/Math.max(0.25, r.cps); }
function planWeek(mode, days, shuffle){
  let pool = DATA.recipes.filter(r => r.category === 'main');
  if (mode === 'budget') pool.sort((a,b) => a.cps-b.cps || a.time-b.time);
  else if (mode === 'quick') pool.sort((a,b) => a.time-b.time || a.cps-b.cps);
  else pool.sort((a,b) => a.cps-b.cps);
  const cap0 = mode === 'variety' ? 1 : 2;
  let chosen = [], slugs = new Set(), cc = {};
  function pick(cap){
    let elig = pool.filter(r => !slugs.has(r.slug) && (cc[r.cuisine]||0) < cap);
    if (!elig.length) return false;
    let r;
    if (shuffle){
      let tot = elig.reduce((s,x) => s+weight(x,mode), 0), rnd = Math.random()*tot, up = 0;
      r = elig[elig.length-1];
      for (const e of elig){ up += weight(e,mode); if (up >= rnd){ r = e; break; } }
    } else r = elig[0];
    chosen.push(r); slugs.add(r.slug); cc[r.cuisine] = (cc[r.cuisine]||0)+1; return true;
  }
  let cap = cap0;
  while (chosen.length < days){ if (!pick(cap)){ cap++; if (cap > days+cap0) break; } }
  return chosen.slice(0, days);
}
const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
function doPlan(shuffle){
  const mode = document.getElementById('mode').value;
  const days = +document.getElementById('days').value;
  currentPlan = planWeek(mode, days, shuffle);
  const total = currentPlan.reduce((s,r) => s+r.cost, 0);
  document.getElementById('planSummary').textContent =
    `${currentPlan.length} dinners · est. $${total.toFixed(0)} groceries · mode: ${mode}`;
  document.getElementById('planList').innerHTML = currentPlan.map((r,i) => `
    <div class="card">
      <div class="day">${DAYS[i] || 'Day '+(i+1)}</div>
      <div><strong>${r.title}</strong></div>
      <div class="meta">${r.cuisine} · ${r.time} min · $${r.cost} ($${r.cps.toFixed(2)}/serving) · serves ${r.servings}</div>
      <div class="meta">📹 <a href="#" data-howto="${r.slug}">how-to</a>${r.video ? ` · <a href="${r.video}" target="_blank">original reel</a>` : ''}</div>
    </div>`).join('');
}

// --- shopping ---
function renderStaples(){
  const wrap = document.getElementById('staples');
  let html = '';
  for (const [aisle, items] of Object.entries(DATA.staples)){
    html += `<div class="aisle"><h3>${aisle}</h3><div class="cols">`;
    items.forEach((it, idx) => {
      const id = `st_${aisle.replace(/\W/g,'')}_${idx}`;
      html += `<label class="chk"><input type="checkbox" id="${id}" data-aisle="${aisle}" data-item="${it.item.replace(/"/g,'&quot;')}" ${it.default ? 'checked' : ''}> ${it.item}</label>`;
    });
    html += `</div></div>`;
  }
  wrap.innerHTML = html;
}
function checkDefaults(){ renderStaples(); document.getElementById('shopOut').innerHTML = ''; }
function buildList(){
  const buckets = {}; const seen = new Set();
  const add = (text, aisle) => {
    const key = text.toLowerCase().replace(/\s+/g,' ').trim();
    if (!key || seen.has(key)) return; seen.add(key);
    (buckets[aisle] = buckets[aisle] || []).push(text);
  };
  document.querySelectorAll('#staples input:checked').forEach(c =>
    add(c.dataset.item, c.dataset.aisle));
  if (document.getElementById('incPlan').checked)
    currentPlan.forEach(r => r.ingredients.forEach(ing => add(ing.t, ing.a)));
  let html = '', plain = '';
  DATA.aisleOrder.forEach(aisle => {
    if (!buckets[aisle]) return;
    html += `<div class="aisle"><h3>${aisle}</h3>`;
    plain += `\n${aisle}\n`;
    buckets[aisle].forEach(item => {
      html += `<label class="chk"><input type="checkbox"> ${item}</label>`;
      plain += `  - ${item}\n`;
    });
    html += `</div>`;
  });
  if (!html) html = '<p class="muted">Nothing selected yet — check some usuals or generate a plan first.</p>';
  else html += `<button id="copyBtn" style="margin-top:8px">📋 Copy list</button>`;
  document.getElementById('shopOut').innerHTML = html;
  lastList = plain;
  const cb = document.getElementById('copyBtn');
  if (cb) cb.addEventListener('click', () => navigator.clipboard.writeText(lastList));
}
let lastList = '';

// --- browse ---
function renderBrowse(){
  const sort = document.getElementById('sort').value;
  let rs = [...DATA.recipes];
  if (sort === 'cps') rs.sort((a,b) => a.cps-b.cps);
  else if (sort === 'time') rs.sort((a,b) => a.time-b.time);
  else rs.sort((a,b) => a.cuisine.localeCompare(b.cuisine) || a.title.localeCompare(b.title));
  document.getElementById('browseList').innerHTML = `<div class="grid">` + rs.map(r => `
    <div class="card recipe-row">
      <div><strong>${r.title}</strong><div class="muted">${r.cuisine} · ${r.time} min · $${r.cps.toFixed(2)}/serving</div></div>
      <div style="text-align:right"><a href="#" data-howto="${r.slug}">📹 how-to</a></div>
    </div>`).join('') + `</div>`;
}

// --- how-to modal ---
let htSteps = [], htI = 0, htPlaying = false, htTimer = null;
function bySlug(slug){ return DATA.recipes.find(r => r.slug === slug); }
function openHowTo(slug){
  const r = bySlug(slug); if (!r) return;
  htSteps = r.steps || []; htI = 0; htPlaying = false;
  document.getElementById('mTitle').textContent = r.title;
  document.getElementById('mReel').innerHTML = r.embed
    ? `<iframe src="${r.embed}" scrolling="no" allowtransparency="true"></iframe>`
    : (r.video ? `<a href="${r.video}" target="_blank">▶ Watch the original reel</a>` : '');
  document.getElementById('modal').classList.add('open');
  renderHowTo();
}
function closeHowTo(){
  htStop();
  document.getElementById('modal').classList.remove('open');
  document.getElementById('mReel').innerHTML = '';  // stop the reel
}
function renderHowTo(){
  if (!htSteps.length){ document.getElementById('mStep').textContent = 'Steps are in the reel →';
    document.getElementById('mCounter').textContent = ''; return; }
  document.getElementById('mStep').textContent = htSteps[htI];
  document.getElementById('mCounter').textContent = 'STEP ' + (htI+1) + ' / ' + htSteps.length;
  document.getElementById('mFill').style.width = '0%';
}
function htDwell(){ return Math.max(5, Math.min(20, Math.round(htSteps[htI].split(' ').length / 2.5))) * 1000; }
function htSchedule(){
  clearTimeout(htTimer);
  const fill = document.getElementById('mFill'); const dur = htDwell(); const start = Date.now();
  (function tick(){ if (!htPlaying) return;
    fill.style.width = Math.min(100, (Date.now()-start)/dur*100) + '%';
    if (Date.now()-start < dur) requestAnimationFrame(tick); })();
  htTimer = setTimeout(() => { if (htI < htSteps.length-1){ htI++; renderHowTo(); htSchedule(); } else htStop(); }, dur);
}
function htPlay(){ if (!htSteps.length) return; htPlaying = true;
  document.getElementById('mPlay').textContent = '⏸ Pause'; htSchedule(); }
function htStop(){ htPlaying = false; clearTimeout(htTimer);
  const p = document.getElementById('mPlay'); if (p) p.textContent = '▶ Play'; }
function howToToggle(){ htPlaying ? htStop() : htPlay(); }
function howToStep(d){ const n = htI + d;
  if (n >= 0 && n < htSteps.length){ htI = n; renderHowTo(); if (htPlaying) htSchedule(); } }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHowTo(); });

// --- wire all handlers from JS (no inline on* attributes) ---
document.getElementById('genBtn').addEventListener('click', () => doPlan(false));
document.getElementById('shufBtn').addEventListener('click', () => doPlan(true));
document.getElementById('buildBtn').addEventListener('click', buildList);
document.getElementById('resetBtn').addEventListener('click', checkDefaults);
document.getElementById('sort').addEventListener('change', renderBrowse);
document.getElementById('mClose').addEventListener('click', closeHowTo);
document.getElementById('mPrev').addEventListener('click', () => howToStep(-1));
document.getElementById('mNext').addEventListener('click', () => howToStep(1));
document.getElementById('mPlay').addEventListener('click', howToToggle);
document.getElementById('modal').addEventListener('click', e => {
  if (e.target.id === 'modal') closeHowTo(); });
// Event delegation for dynamically-rendered "how-to" links.
document.body.addEventListener('click', e => {
  const a = e.target.closest('[data-howto]');
  if (a) { e.preventDefault(); openHowTo(a.dataset.howto); }
});

renderStaples(); renderBrowse(); doPlan(false);
</script>
</body>
</html>
"""


# Keywords that must match as whole words, so "minced" doesn't read as "mince"
# (which would misroute "garlic cloves, minced" into Meat & Seafood).
WHOLE_WORD = {"mince", "ground", "egg", "cream"}


def _matches(keyword: str, text: str) -> bool:
    if keyword in WHOLE_WORD:
        # Whole word, but allow a plural 's'/'es' (egg -> eggs, mince -> minces).
        return re.search(rf"\b{re.escape(keyword)}(?:e?s)?\b", text) is not None
    return keyword in text


def aisle_for(item: str) -> str:
    low = item.lower()
    for aisle, keywords in AISLE_RULES:
        if any(_matches(kw, low) for kw in keywords):
            return aisle
    return "Other"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _weight(recipe: dict, mode: str) -> float:
    if mode == "budget":
        return 1.0 / max(0.25, recipe["cost_per_serving"])
    if mode == "quick":
        return 1.0 / max(5, recipe["time_min"])
    return 1.0  # variety: handled by the cuisine cap below


def select_plan(recipes: list[dict], days: int, mode: str, shuffle: bool,
                seed: int | None, pins: list[str], excludes: set[str]) -> list[dict]:
    pool = [r for r in recipes
            if r.get("category") == "main" and r["slug"] not in excludes]
    chosen: list[dict] = []
    chosen_slugs: set[str] = set()
    cuisine_count: dict[str, int] = {}

    # Pins first (forced in, ignore caps).
    index = by_slug(recipes)
    for slug in pins:
        r = index.get(slug)
        if r and slug not in chosen_slugs:
            chosen.append(r)
            chosen_slugs.add(slug)
            cuisine_count[r["cuisine"]] = cuisine_count.get(r["cuisine"], 0) + 1

    # Cuisine cap: variety => 1 per cuisine until exhausted; else 2.
    base_cap = 1 if mode == "variety" else 2

    candidates = [r for r in pool if r["slug"] not in chosen_slugs]
    if mode == "budget":
        candidates.sort(key=lambda r: (r["cost_per_serving"], r["time_min"]))
    elif mode == "quick":
        candidates.sort(key=lambda r: (r["time_min"], r["cost_per_serving"]))
    else:  # variety
        candidates.sort(key=lambda r: r["cost_per_serving"])

    rng = random.Random(seed if seed is not None else (None if shuffle else 0))

    def pick_round(cap: int) -> bool:
        nonlocal chosen, chosen_slugs, cuisine_count
        eligible = [r for r in candidates
                    if r["slug"] not in chosen_slugs
                    and cuisine_count.get(r["cuisine"], 0) < cap]
        if not eligible:
            return False
        if shuffle:
            weights = [_weight(r, mode) for r in eligible]
            r = rng.choices(eligible, weights=weights, k=1)[0]
        else:
            r = eligible[0]  # already mode-sorted => deterministic best
        chosen.append(r)
        chosen_slugs.add(r["slug"])
        cuisine_count[r["cuisine"]] = cuisine_count.get(r["cuisine"], 0) + 1
        return True

    cap = base_cap
    while len(chosen) < days:
        if not pick_round(cap):
            cap += 1  # loosen the cuisine cap if we run out of variety
            if cap > days + base_cap:
                break
    return chosen[:days]


# ---------------------------------------------------------------------------
# Shopping list
# ---------------------------------------------------------------------------

def build_shopping_list(slugs: list[str],
                        extra_items: list[str] | None = None) -> dict[str, list[str]]:
    aisles: dict[str, list[str]] = {a: [] for a in AISLE_ORDER}
    seen: set[str] = set()

    def add(item: str) -> None:
        key = re.sub(r"\s+", " ", item.lower()).strip()
        if not key or key in seen:
            return
        seen.add(key)
        aisles.setdefault(aisle_for(item), []).append(item)

    for slug in slugs:
        for item in parse_ingredients(slug):
            add(item)
    for item in extra_items or []:
        add(item)
    return {a: items for a, items in aisles.items() if items}


def load_staples() -> dict[str, list[dict]]:
    if not STAPLES_FILE.exists():
        return {}
    data = yaml.safe_load(STAPLES_FILE.read_text()) or {}
    return data.get("staples", {})


def print_staples(defaults_only: bool = False) -> None:
    staples = load_staples()
    if not staples:
        print(f"No staples file at {STAPLES_FILE}")
        return
    print("\n🧺  Your usual items"
          + (" (defaults)" if defaults_only else "") + "\n")
    for aisle, items in staples.items():
        rows = [it for it in items if it.get("default")] if defaults_only else items
        if not rows:
            continue
        print(f"  {aisle}")
        for it in rows:
            mark = "★" if it.get("default") else " "
            print(f"    {mark} {it['item']}")
        print()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_plan(chosen: list[dict], mode: str, show_list: bool) -> None:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    total_cost = sum(r["est_cost_usd"] for r in chosen)
    print(f"\n🍽  Weekly Meal Plan  ·  mode: {mode}  ·  {len(chosen)} dinners\n")
    for i, r in enumerate(chosen):
        day = days[i] if i < len(days) else f"Day {i + 1}"
        print(f"  {day}  {r['title']}")
        print(f"        {r['cuisine']} · {r['time_min']} min · "
              f"${r['est_cost_usd']:.0f} (${r['cost_per_serving']:.2f}/serving) · "
              f"serves {r['servings']}")
        vid = video_link(r["slug"])
        if vid:
            print(f"        📹 How-to video: {vid}")
        print()
    avg = total_cost / len(chosen) if chosen else 0
    print(f"  ── Est. groceries: ${total_cost:.0f} total "
          f"(avg ${avg:.0f}/recipe) ──\n")

    if show_list:
        print_shopping_list([r["slug"] for r in chosen])


def print_shopping_list(slugs: list[str]) -> None:
    lst = build_shopping_list(slugs)
    print("🛒  Shopping List\n")
    for aisle in AISLE_ORDER:
        if aisle in lst:
            print(f"  {aisle}")
            for item in lst[aisle]:
                print(f"    ☐ {item}")
            print()


def write_shopping_list_md(aisles: dict[str, list[str]], slugs: list[str],
                           title: str) -> Path:
    from datetime import date
    SHOPPING_DIR.mkdir(exist_ok=True)
    lines = [f"# 🛒 Shopping List — {title}", "", f"_{date.today().isoformat()}_", ""]
    for aisle in AISLE_ORDER:
        if aisle in aisles:
            lines.append(f"## {aisle}")
            for item in aisles[aisle]:
                lines.append(f"- [ ] {item}")
            lines.append("")
    if slugs:
        index = by_slug(load_recipes())
        lines.append("## Meals included")
        for slug in slugs:
            r = index.get(slug)
            title_txt = r["title"] if r else slug
            vid = video_link(slug)
            vid_txt = f" — [📹 how-to]({vid})" if vid else ""
            lines.append(f"- [{title_txt}](../recipes/{slug}.md){vid_txt}")
        lines.append("")
    out = SHOPPING_DIR / f"list-{date.today().isoformat()}.md"
    out.write_text("\n".join(lines))
    return out


def cmd_shop(recipes: list[dict], args: argparse.Namespace) -> int:
    slugs = list(args.recipes)
    if args.plan_days:
        plan = select_plan(recipes, args.plan_days, args.plan_mode,
                           args.shuffle, args.seed, [], set())
        for r in plan:
            if r["slug"] not in slugs:
                slugs.append(r["slug"])
    valid = {r["slug"] for r in recipes}
    unknown = [s for s in slugs if s not in valid]
    if unknown:
        print(f"Unknown recipe(s): {', '.join(unknown)}")
        return 1
    aisles = build_shopping_list(slugs, args.add)
    if not aisles:
        print("Nothing to shop for — add staples (--add) or recipes (--recipes).")
        return 1
    out = write_shopping_list_md(aisles, slugs, args.title)
    print_shopping_list_aisles(aisles)
    print(f"📝 Saved to {out}")
    return 0


def print_shopping_list_aisles(aisles: dict[str, list[str]]) -> None:
    print("🛒  Shopping List\n")
    for aisle in AISLE_ORDER:
        if aisle in aisles:
            print(f"  {aisle}")
            for item in aisles[aisle]:
                print(f"    ☐ {item}")
            print()


def print_list(recipes: list[dict], sort: str) -> None:
    if sort == "cost":
        recipes = sorted(recipes, key=lambda r: r["cost_per_serving"])
    elif sort == "time":
        recipes = sorted(recipes, key=lambda r: r["time_min"])
    else:
        recipes = sorted(recipes, key=lambda r: (r["cuisine"], r["title"]))
    print(f"\n{len(recipes)} recipes\n")
    for r in recipes:
        tags = f"  [{', '.join(r['tags'])}]" if r.get("tags") else ""
        print(f"  {r['slug']:<42} {r['cuisine']:<14} "
              f"{r['time_min']:>3}min  ${r['cost_per_serving']:.2f}/srv{tags}")
    print()


def generate_readme(recipes: list[dict]) -> str:
    lines = ["# 🍳 Recipe Collection", "",
             f"{len(recipes)} recipes saved from Instagram, browsable below. "
             "Run the **Cookbook app** for the full planner + shopping experience:", "",
             "```bash",
             "python tools/meal_planner.py cookbook   # generate cookbook.html (open in browser)",
             "```", "",
             "Or use the CLI directly:", "",
             "```bash",
             "python tools/meal_planner.py plan --mode budget   # cheapest week",
             "python tools/meal_planner.py plan --mode variety  # mix of cuisines",
             "python tools/meal_planner.py plan --mode quick    # fastest to cook",
             "python tools/meal_planner.py plan --shuffle        # reshuffle picks",
             "python tools/meal_planner.py video <slug>          # build a how-to player",
             "```", "",
             "Each recipe links its original Instagram reel, and "
             "`video <slug>` (or `video all`) generates a self-contained HTML "
             "how-to player in `recipes/videos/` — the reel embedded next to an "
             "auto-advancing, step-by-step slideshow. Open it in any browser.", ""]
    by_cuisine: dict[str, list[dict]] = {}
    for r in recipes:
        by_cuisine.setdefault(r["cuisine"], []).append(r)
    for cuisine in sorted(by_cuisine):
        lines.append(f"## {cuisine}")
        lines.append("")
        lines.append("| Recipe | Time | Cost/serving | Tags |")
        lines.append("|--------|------|--------------|------|")
        for r in sorted(by_cuisine[cuisine], key=lambda r: r["title"]):
            tags = ", ".join(r["tags"]) if r.get("tags") else ""
            lines.append(f"| [{r['title']}]({r['file']}) | {r['time_min']} min "
                         f"| ${r['cost_per_serving']:.2f} | {tags} |")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="build a weekly plan + shopping list")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--mode", choices=["budget", "variety", "quick"], default="budget")
    p.add_argument("--shuffle", action="store_true", help="reshuffle the picks")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--pin", nargs="*", default=[], help="force-include slug(s)")
    p.add_argument("--exclude", nargs="*", default=[], help="exclude slug(s)")
    p.add_argument("--no-shopping-list", action="store_true")

    lp = sub.add_parser("list", help="list all recipes")
    lp.add_argument("--sort", choices=["cost", "time", "cuisine"], default="cost")

    sp = sub.add_parser("shopping", help="shopping list for specific recipes")
    sp.add_argument("slugs", nargs="+")

    sub.add_parser("readme", help="regenerate recipes/README.md")

    vp = sub.add_parser("video", help="generate a how-to video player (HTML) for recipe(s)")
    vp.add_argument("slugs", nargs="+", help="recipe slug(s), or 'all'")

    sub.add_parser("cookbook", help="generate the Cookbook app (cookbook.html)")

    stp = sub.add_parser("staples", help="show your usual shopping items")
    stp.add_argument("--defaults", action="store_true", help="only the pre-checked items")

    shp = sub.add_parser("shop", help="build a combined shopping list (staples + recipes)")
    shp.add_argument("--add", nargs="*", default=[], help="staple/free-text items to include")
    shp.add_argument("--recipes", nargs="*", default=[], help="recipe slug(s) to include")
    shp.add_argument("--plan-days", type=int, default=0, help="also fold in an auto meal plan")
    shp.add_argument("--plan-mode", choices=["budget", "variety", "quick"], default="budget")
    shp.add_argument("--shuffle", action="store_true")
    shp.add_argument("--seed", type=int, default=None)
    shp.add_argument("--title", default="This Week")

    args = parser.parse_args(argv)
    recipes = load_recipes()

    if args.command == "plan":
        chosen = select_plan(recipes, args.days, args.mode, args.shuffle,
                             args.seed, args.pin, set(args.exclude))
        if not chosen:
            print("No recipes matched.")
            return 1
        print_plan(chosen, args.mode, not args.no_shopping_list)
    elif args.command == "list":
        print_list(recipes, args.sort)
    elif args.command == "shopping":
        valid = {r["slug"] for r in recipes}
        unknown = [s for s in args.slugs if s not in valid]
        if unknown:
            print(f"Unknown recipe(s): {', '.join(unknown)}")
            return 1
        print_shopping_list(args.slugs)
    elif args.command == "readme":
        out = RECIPES_DIR / "README.md"
        out.write_text(generate_readme(recipes) + "\n")
        print(f"Wrote {out}")
    elif args.command == "video":
        valid = {r["slug"] for r in recipes}
        slugs = sorted(valid) if args.slugs == ["all"] else args.slugs
        unknown = [s for s in slugs if s not in valid]
        if unknown:
            print(f"Unknown recipe(s): {', '.join(unknown)}")
            return 1
        video_dir = RECIPES_DIR / "videos"
        video_dir.mkdir(exist_ok=True)
        for slug in slugs:
            out = video_dir / f"{slug}.html"
            out.write_text(generate_video_html(slug))
            print(f"📹 {out}")
        print(f"\nOpen any file in a browser to play the how-to.")
    elif args.command == "cookbook":
        out = REPO_DIR / "cookbook.html"
        out.write_text(generate_cookbook_html(recipes))
        print(f"🍳 Cookbook app generated: {out}\n   Open it in a browser to "
              f"plan, shop, and browse.")
    elif args.command == "staples":
        print_staples(defaults_only=args.defaults)
    elif args.command == "shop":
        return cmd_shop(recipes, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
