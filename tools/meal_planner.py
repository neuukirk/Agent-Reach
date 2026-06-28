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

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"
INDEX_FILE = RECIPES_DIR / "index.yaml"

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
    ("Produce", ["onion", "garlic", "ginger", "tomato", "lettuce", "cucumber",
                 "jalap", "bell pepper", "potato", "carrot", "celery", "spinach",
                 "coriander", "cilantro", "parsley", "scallion", "spring onion",
                 "green onion", "lemon", "lime", "orange", "chive", "basil",
                 "thyme", "avocado", "mushroom", "rosemary", "fresno", "dill"]),
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
                          "seasoning", "sauce", "paste", "nutmeg", "pea"]),
]

AISLE_ORDER = ["Produce", "Meat & Seafood", "Dairy & Eggs", "Pantry & Canned",
               "Frozen", "Other"]

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


# Keywords that must match as whole words, so "minced" doesn't read as "mince"
# (which would misroute "garlic cloves, minced" into Meat & Seafood).
WHOLE_WORD = {"mince", "ground", "egg", "cream"}


def _matches(keyword: str, text: str) -> bool:
    if keyword in WHOLE_WORD:
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
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

def build_shopping_list(slugs: list[str]) -> dict[str, list[str]]:
    aisles: dict[str, list[str]] = {a: [] for a in AISLE_ORDER}
    seen: set[str] = set()
    for slug in slugs:
        for item in parse_ingredients(slug):
            key = re.sub(r"\s+", " ", item.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            aisles.setdefault(aisle_for(item), []).append(item)
    return {a: items for a, items in aisles.items() if items}


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
             "Use the meal planner to build a week + shopping list:", "",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
