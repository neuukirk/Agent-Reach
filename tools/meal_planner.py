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
import math
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
             "```", ""]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
