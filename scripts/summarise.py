#!/usr/bin/env python3
"""Print achieved macros against target, with the error, for a result document.

Kept separate from the solver so verify.sh reads the numbers back out of the
written document rather than out of whatever the solver had in memory.
"""

from __future__ import annotations

import json
import sys


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: summarise.py <result.json>", file=sys.stderr)
        return 2
    with open(argv[1]) as fh:
        doc = json.load(fh)

    print(f"status: {doc['status']}")
    if doc["status"] != "solved":
        for cert in doc.get("certificates", []):
            print(f"  {cert['constraint']}: {cert['message']}")
        return 0

    keys = list(doc["per_day"][0]["macros"])
    print()
    header = f"{'day':>4} " + " ".join(
        f"{doc['per_day'][0]['macros'][k]['label']:>22}" for k in keys
    )
    print(header)
    print(f"{'':>4} " + " ".join(f"{'target  achieved  error':>22}" for _ in keys))
    for day in doc["per_day"]:
        cells = []
        for k in keys:
            m = day["macros"][k]
            cells.append(f"{m['target']:>7.1f} {m['achieved']:>8.1f} {m['error']:>+7.1f}")
        print(f"{day['day']:>4} " + " ".join(cells))

    print()
    for k in keys:
        vals = [d["macros"][k] for d in doc["per_day"]]
        errs = [v["error"] for v in vals]
        worst = max(errs, key=abs)
        mean = sum(errs) / len(errs)
        allowance = vals[0]["allowance"]
        met = sum(1 for v in vals if v["met"])
        mark = "[ok] MET" if met == len(vals) else "[X] MISSED"
        print(
            f"  {vals[0]['label']:<20} target {vals[0]['target']:>8.1f}  "
            f"allowance {allowance:>7.1f}  mean error {mean:>+8.2f}  "
            f"worst error {worst:>+8.2f}  {met}/{len(vals)} days {mark}"
        )

    print()
    print(f"  distinct foods used {doc['distinct_foods']} "
          f"(minimum {doc['instance']['min_distinct_foods']})")
    worst_meal = max(doc["meal_repeats"].items(), key=lambda kv: kv[1])
    print(f"  most repeated meal used {worst_meal[1]} time(s) "
          f"(limit {doc['instance']['max_meal_repeats']})")
    caps = doc["instance"]["frequency_caps"]
    for food, cap in sorted(caps.items()):
        used = doc["food_usage"].get(food, 0)
        print(f"  {food:<18} used {used:>2} of {cap:>2} allowed meal slots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
