"""Turning a solve result into the JSON document everything else reads.

The independent checker in checker/independent_check.py consumes this document
and recomputes every number in it from data/foods.json. Nothing here is trusted
downstream.
"""

from __future__ import annotations

import json

from .model import MACRO_LABELS, Instance


def plan_to_dict(inst: Instance, plan) -> dict:
    s = inst.meals_per_day
    days = []
    for d in range(inst.days):
        meals = []
        for j in range(s):
            meal = plan[d * s + j]
            meals.append({"slot": j + 1, "items": meal.to_dict()["items"]})
        days.append({"day": d + 1, "meals": meals})
    return {"days": days}


def day_totals(inst: Instance, table, plan, day: int) -> dict:
    s = inst.meals_per_day
    out = {k: 0.0 for k in inst.targets}
    for j in range(s):
        for food_id, n in plan[day * s + j].items:
            for k in out:
                out[k] += table[food_id].amount(k) * n
    return out


def build(inst: Instance, table, result, food_table_path: str) -> dict:
    doc = {
        "tool": "macro-solver",
        "framing": (
            "The targets below were supplied by the person running this tool or copied "
            "by them from their clinician. This tool does not choose targets, does not "
            "check whether they are appropriate for anyone, and is not nutrition advice."
        ),
        "food_table": food_table_path,
        "food_table_source": table.dataset,
        "instance": inst.to_dict(),
        "status": result.status,
        "notes": result.notes,
        "certificates": [c.to_dict() for c in result.certificates],
    }

    if not result.plan:
        return doc

    doc["plan"] = plan_to_dict(inst, result.plan)

    per_day = []
    for d in range(inst.days):
        totals = day_totals(inst, table, result.plan, d)
        macros = {}
        for k, t in inst.targets.items():
            achieved = totals[k]
            macros[k] = {
                "label": MACRO_LABELS.get(k, k),
                "target": t.target,
                "achieved": round(achieved, 3),
                "error": round(achieved - t.target, 3),
                "allowance": round(t.allowance, 3),
                "mode": t.mode,
                "outside_band_by": round(t.deviation(achieved), 3),
                "met": t.met(achieved),
            }
        per_day.append({"day": d + 1, "macros": macros})
    doc["per_day"] = per_day

    usage = {}
    repeats = {}
    for meal in result.plan:
        key = meal.label()
        repeats[key] = repeats.get(key, 0) + 1
        for food_id, _ in meal.items:
            usage[food_id] = usage.get(food_id, 0) + 1
    doc["food_usage"] = dict(sorted(usage.items()))
    doc["meal_repeats"] = dict(sorted(repeats.items(), key=lambda kv: (-kv[1], kv[0])))
    doc["distinct_foods"] = len(usage)
    doc["status_all_targets_met"] = all(
        m["met"] for day in per_day for m in day["macros"].values()
    )
    return doc


def render_text(doc: dict) -> str:
    lines = []
    lines.append("macro-solver")
    lines.append("=" * 60)
    lines.append(doc["framing"])
    lines.append("")
    lines.append(f"status: {doc['status']}")
    lines.append("")

    if doc["certificates"]:
        lines.append("Why no plan can exist (proven, not guessed):")
        for c in doc["certificates"]:
            lines.append(f"  [{c['constraint']}]")
            lines.append(f"    {c['message']}")
        lines.append("")

    if doc["status"] == "not_found":
        lines.append("The search did not find a plan. No bound was violated, so this is")
        lines.append("not a proof that no plan exists. Closest attempt missed:")
        for m in doc["notes"].get("closest_misses", [])[:12]:
            lines.append(
                f"  day {m['day']} {m['macro']}: achieved {m['achieved']}, "
                f"target {m['target']}, outside the band by {m['outside_band_by']}"
            )
        lines.append("")

    if "plan" not in doc:
        return "\n".join(lines)

    for day, per in zip(doc["plan"]["days"], doc["per_day"]):
        lines.append(f"Day {day['day']}")
        for meal in day["meals"]:
            parts = [
                f"{it['servings']}x {it['food_id']}" if it["servings"] > 1 else it["food_id"]
                for it in meal["items"]
            ]
            lines.append(f"  meal {meal['slot']}: " + " + ".join(parts))
        for k, m in per["macros"].items():
            mark = "MET  [ok]" if m["met"] else "MISS [X]"
            lines.append(
                f"    {m['label']:<20} target {m['target']:>8.1f}  "
                f"achieved {m['achieved']:>8.1f}  error {m['error']:>+8.1f}  "
                f"allowance +/-{m['allowance']:.1f}  {mark}"
            )
        lines.append("")

    lines.append(f"distinct foods used: {doc['distinct_foods']} "
                 f"(minimum {doc['instance']['min_distinct_foods']})")
    lines.append("food appearances across the plan (cap in brackets where set):")
    caps = doc["instance"]["frequency_caps"]
    for food_id, n in doc["food_usage"].items():
        cap = f" [cap {caps[food_id]}]" if food_id in caps else ""
        lines.append(f"  {food_id:<18} {n}{cap}")
    worst = next(iter(doc["meal_repeats"].items()), None)
    if worst:
        lines.append(
            f"most repeated meal: {worst[0]} used {worst[1]} time(s) "
            f"(limit {doc['instance']['max_meal_repeats']})"
        )
    return "\n".join(lines)


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2) + "\n"
