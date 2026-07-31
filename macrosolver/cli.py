"""Command line entry point.

Exit codes:
  0  a plan was found and every constraint holds
  1  no plan exists, proven by a bound in bounds.py
  2  the search did not find a plan and nothing was proven
  3  the request itself is malformed
"""

from __future__ import annotations

import argparse
import os
import sys

from . import report
from .foods import DEFAULT_TABLE, load_table
from .model import Instance
from .solver import solve

EXIT_SOLVED = 0
EXIT_PROVEN_INFEASIBLE = 1
EXIT_NOT_FOUND = 2
EXIT_BAD_REQUEST = 3

DISCLAIMER = (
    "macro-solver arranges foods to hit macro targets that you supplied. It does not "
    "recommend targets, does not check whether your targets are appropriate for you or "
    "for anyone, and is not nutrition or medical advice. Food composition varies between "
    "real items and the reference values used here."
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m macrosolver",
        description="Solve a week of meals against macro targets you supply.",
        epilog=DISCLAIMER,
    )
    ap.add_argument("instance", help="path to an instance JSON file")
    ap.add_argument("--foods", default=DEFAULT_TABLE, help="path to the food table")
    ap.add_argument("--json", action="store_true", help="print the result document")
    ap.add_argument("--out", help="write the result document to this path")
    ap.add_argument("--seed", type=int, help="override the instance seed")
    ap.add_argument("--restarts", type=int, default=4)
    ap.add_argument("--max-sweeps", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        table = load_table(args.foods)
    except (OSError, ValueError, KeyError) as exc:
        print(f"food table could not be loaded: {exc}", file=sys.stderr)
        return EXIT_BAD_REQUEST

    try:
        inst = Instance.load(args.instance)
        if args.seed is not None:
            inst.seed = args.seed
        inst.validate(table.ids())
    except (OSError, ValueError, KeyError) as exc:
        print(f"instance could not be read: {exc}", file=sys.stderr)
        return EXIT_BAD_REQUEST

    result = solve(inst, table, restarts=args.restarts, max_sweeps=args.max_sweeps)
    doc = report.build(inst, table, result, os.path.relpath(args.foods, os.getcwd()))

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report.dumps(doc))
    if args.json:
        sys.stdout.write(report.dumps(doc))
    elif not args.quiet:
        print(report.render_text(doc))
        print()
        print(DISCLAIMER)

    return {
        "solved": EXIT_SOLVED,
        "proven_infeasible": EXIT_PROVEN_INFEASIBLE,
        "not_found": EXIT_NOT_FOUND,
    }[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
