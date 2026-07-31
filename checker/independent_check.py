#!/usr/bin/env python3
"""Independent constraint checker for a macro-solver result document.

This file deliberately imports nothing from the `macrosolver` package. It reads
the result JSON and the food table JSON straight off disk and recomputes every
constraint from the raw per-serving numbers. A checker that shares code with the
solver inherits the solver's bugs and reports clean on output that is not.

What it re-derives, from scratch:

  * every daily macro total, by multiplying servings by the food table's
    per-serving values
  * the accepted window for every target, from the target and its tolerance
  * whether each target is met, and by how much it misses when it is not
  * how many meal slots contain each food, against the frequency caps
  * how often each distinct meal composition recurs, against the repeat limit
  * how many distinct foods the plan uses, against the minimum
  * the structural limits: number of days, meals per day, foods per meal,
    servings per food in a meal, no food twice in the same meal

It also checks the numbers the solver *reported* against the numbers recomputed
here, so a solver that returns a valid plan but lies about its macros is caught
as well as one that returns an invalid plan.

Exit codes:
  0  the document is internally consistent and every constraint holds
  1  at least one constraint or reported number is wrong
  2  the document could not be read
"""

from __future__ import annotations

import argparse
import json
import os
import sys

TOL = 1e-6


class Findings:
    def __init__(self):
        self.problems = []
        self.checks = 0

    def check(self, ok: bool, message: str) -> bool:
        self.checks += 1
        if not ok:
            self.problems.append(message)
        return ok


def accepted_window(spec):
    """Recompute the accepted window for a target from the instance's own spec."""
    target = float(spec["target"])
    tol_abs = float(spec.get("tol_abs", 0.0))
    tol_pct = float(spec.get("tol_pct", 0.0))
    allowance = tol_abs
    pct_allowance = target * tol_pct / 100.0
    if pct_allowance > allowance:
        allowance = pct_allowance
    mode = spec.get("mode", "band")
    lo = None if mode == "max" else target - allowance
    hi = None if mode == "min" else target + allowance
    return lo, hi, allowance


def miss_amount(value, lo, hi):
    if lo is not None and value < lo - TOL:
        return lo - value
    if hi is not None and value > hi + TOL:
        return value - hi
    return 0.0


def meal_key(items):
    return " | ".join(
        f"{it['food_id']}x{int(it['servings'])}"
        for it in sorted(items, key=lambda it: it["food_id"])
    )


def run(doc, food_table_path, find: Findings):
    with open(food_table_path) as fh:
        raw_table = json.load(fh)
    per_serving = {}
    for row in raw_table["foods"]:
        per_serving[row["id"]] = row["per_serving"]

    inst = doc["instance"]
    status = doc["status"]

    if status != "solved":
        find.check(
            "plan" not in doc or status in ("not_found",),
            f"status {status!r} must not come with a plan presented as a solution",
        )
        if status == "proven_infeasible":
            find.check(
                bool(doc.get("certificates")),
                "status is proven_infeasible but no certificate was given, "
                "so nothing was actually proven",
            )
            for cert in doc.get("certificates", []):
                find.check(
                    bool(cert.get("constraint")) and bool(cert.get("message")),
                    "a certificate names neither a constraint nor a reason",
                )
        return

    find.check("plan" in doc, "status is solved but the document carries no plan")
    if "plan" not in doc:
        return
    find.check("per_day" in doc, "status is solved but no per-day macro report was given")

    days = doc["plan"]["days"]
    n_days = int(inst["days"])
    per_day_slots = int(inst["meals_per_day"])
    max_foods = int(inst["max_foods_per_meal"])
    max_servings = int(inst["max_servings_per_food_in_meal"])
    pantry = set(inst["pantry"])

    find.check(len(days) == n_days, f"plan has {len(days)} days, instance asks for {n_days}")

    usage = {}
    repeats = {}
    distinct = set()
    recomputed = []

    for day in days:
        meals = day["meals"]
        find.check(
            len(meals) == per_day_slots,
            f"day {day['day']} has {len(meals)} meals, instance asks for {per_day_slots}",
        )
        totals = {}
        for meal in meals:
            items = meal["items"]
            find.check(
                len(items) >= 1,
                f"day {day['day']} meal {meal['slot']} is empty",
            )
            find.check(
                len(items) <= max_foods,
                f"day {day['day']} meal {meal['slot']} holds {len(items)} foods, "
                f"limit is {max_foods}",
            )
            seen = set()
            for it in items:
                fid = it["food_id"]
                servings = int(it["servings"])
                find.check(
                    fid not in seen,
                    f"day {day['day']} meal {meal['slot']} lists {fid} more than once",
                )
                seen.add(fid)
                find.check(fid in pantry, f"{fid} is used but is not in the pantry")
                find.check(
                    fid in per_serving,
                    f"{fid} is used but is not in the food table",
                )
                find.check(
                    1 <= servings <= max_servings,
                    f"day {day['day']} meal {meal['slot']} gives {fid} {servings} servings, "
                    f"limit is {max_servings}",
                )
                if fid in per_serving:
                    for macro, amount in per_serving[fid].items():
                        totals[macro] = totals.get(macro, 0.0) + float(amount) * servings
                usage[fid] = usage.get(fid, 0) + 1
                distinct.add(fid)
            repeats[meal_key(items)] = repeats.get(meal_key(items), 0) + 1
        recomputed.append(totals)

    # Macro targets, recomputed from the food table rather than trusted.
    per_day_report = doc.get("per_day", [])
    find.check(
        len(per_day_report) == len(recomputed),
        f"the report covers {len(per_day_report)} days but the plan has "
        f"{len(recomputed)}",
    )
    for idx, totals in enumerate(recomputed):
        spec_day = per_day_report[idx] if idx < len(per_day_report) else None
        for key, spec in inst["targets"].items():
            lo, hi, allowance = accepted_window(spec)
            got = totals.get(key, 0.0)
            miss = miss_amount(got, lo, hi)
            find.check(
                miss == 0.0,
                f"day {idx + 1} {key}: recomputed {got:.3f}, accepted window is "
                f"[{'-inf' if lo is None else f'{lo:.3f}'}, "
                f"{'+inf' if hi is None else f'{hi:.3f}'}], outside it by {miss:.3f}",
            )
            if spec_day is None:
                continue
            reported = spec_day["macros"].get(key)
            if find.check(
                reported is not None,
                f"day {idx + 1} {key}: the report does not mention this target at all",
            ):
                find.check(
                    abs(float(reported["achieved"]) - got) <= 1e-2,
                    f"day {idx + 1} {key}: report claims {reported['achieved']} but the "
                    f"food table gives {got:.3f}",
                )
                find.check(
                    bool(reported["met"]) == (miss == 0.0),
                    f"day {idx + 1} {key}: report says met={reported['met']} but the "
                    f"recomputation says met={miss == 0.0}",
                )
                find.check(
                    abs(float(reported["allowance"]) - allowance) <= 1e-2,
                    f"day {idx + 1} {key}: report claims allowance "
                    f"{reported['allowance']} but the tolerance gives {allowance:.3f}",
                )

    # Frequency caps.
    default_cap = n_days * per_day_slots
    caps = inst.get("frequency_caps", {})
    for fid, count in sorted(usage.items()):
        cap = int(caps.get(fid, default_cap))
        find.check(
            count <= cap,
            f"frequency cap broken: {fid} appears in {count} meal slots, cap is {cap}",
        )

    # Variety.
    max_repeats = int(inst["max_meal_repeats"])
    for key, count in sorted(repeats.items()):
        find.check(
            count <= max_repeats,
            f"variety broken: the meal '{key}' is used {count} times, limit is {max_repeats}",
        )
    find.check(
        len(distinct) >= int(inst["min_distinct_foods"]),
        f"variety broken: the plan uses {len(distinct)} distinct foods, "
        f"minimum is {inst['min_distinct_foods']}",
    )

    # Numbers the document reported about itself.
    if "distinct_foods" in doc:
        find.check(
            int(doc["distinct_foods"]) == len(distinct),
            f"report claims {doc['distinct_foods']} distinct foods, recount gives "
            f"{len(distinct)}",
        )
    if "food_usage" in doc:
        find.check(
            {k: int(v) for k, v in doc["food_usage"].items()} == usage,
            "report's food usage counts do not match a recount from the plan",
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="path to a macro-solver result JSON document")
    ap.add_argument("--foods", help="path to the food table JSON")
    ap.add_argument("--expect", choices=["solved", "proven_infeasible", "not_found"],
                    help="fail unless the document reports this status")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        with open(args.result) as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"could not read {args.result}: {exc}", file=sys.stderr)
        return 2

    foods = args.foods
    if not foods:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        foods = os.path.join(here, "data", "foods.json")
    if not os.path.exists(foods):
        print(f"food table not found at {foods}", file=sys.stderr)
        return 2

    find = Findings()
    try:
        run(doc, foods, find)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"the document is not shaped like a macro-solver result: {exc!r}",
              file=sys.stderr)
        return 2

    if args.expect:
        find.check(
            doc.get("status") == args.expect,
            f"expected status {args.expect!r}, document says {doc.get('status')!r}",
        )

    if find.problems:
        print(f"INDEPENDENT CHECK FAILED: {len(find.problems)} problem(s) "
              f"out of {find.checks} checks")
        for p in find.problems:
            print(f"  - {p}")
        return 1

    if not args.quiet:
        print(f"INDEPENDENT CHECK PASSED: {find.checks} checks, status "
              f"{doc.get('status')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
