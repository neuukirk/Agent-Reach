---
name: cookbook
description: Launch the Cookbook experience — open the meal planner and run the shopping assistant. Use when the user types /cookbook or asks to plan meals, build a shopping list, run through their usuals, or open their recipe collection.
---

# /cookbook

Kicks off the Cookbook app experience: the meal **planner** plus a **shopping
assistant** baked in, backed by the saved recipes in `recipes/`.

## On invocation

1. **Refresh the app** so it reflects any new recipes or staples:
   ```bash
   python tools/meal_planner.py cookbook   # regenerate cookbook.html
   python tools/meal_planner.py video all  # refresh how-to players
   ```
   Then surface the app to the user with `SendUserFile` on `cookbook.html`
   (it's a self-contained browser app: Planner / Shopping / Recipes tabs).

2. **Offer the two paths** briefly: plan a week, or run a shopping trip (or both).
   Default to cost-optimized planning unless the user asks otherwise.

## Planner flow

- Run `python tools/meal_planner.py plan --mode budget` (or `variety` / `quick`
  per the user's preference; add `--shuffle` to reshuffle, `--days N` to size it).
- Show the week with per-dinner cost, time, and the 📹 how-to link.
- If they like it, carry the chosen recipe slugs into the shopping flow.

## Shopping assistant flow ("run me through my usuals")

1. Read `shopping/staples.yaml`. Present the usuals grouped by aisle, with
   `default: true` items pre-checked, as a quick checklist.
2. Ask the user — in one pass — what to adjust this week: which usuals to drop,
   and anything to add that isn't listed.
3. Ask whether to fold in this week's plan ingredients.
4. Build the combined, aisle-grouped list and save it:
   ```bash
   python tools/meal_planner.py shop \
     --add "Eggs (dozen)" "Milk" "Coffee" [...chosen usuals...] \
     --recipes marry-me-tortellini [...chosen meals...] \
     --title "Week of <date>"
   ```
   (Or use `--plan-days N --plan-mode budget` to auto-include a fresh plan.)
   The list is written to `shopping/list-<date>.md`.
5. Send the saved list with `SendUserFile`.

## Remembering new usuals

If the user names items not already in `shopping/staples.yaml`, offer to add
them (and whether to mark them `default: true`) so future runs include them.
Edit `shopping/staples.yaml` directly to persist.

## Notes
- Slugs = recipe filename without `.md`. `python tools/meal_planner.py list`
  shows them all.
- Keep it conversational; don't force the user through every category if they
  just want the defaults plus a couple of additions.
