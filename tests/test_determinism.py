"""Same inputs and same seed must produce the same plan, every time and in a
fresh process. A solver whose answer drifts cannot be reviewed by anyone."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

from helpers import FOODS, fixture

from macrosolver import report
from macrosolver.foods import load_table
from macrosolver.model import Instance
from macrosolver.solver import solve

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plan_digest(doc):
    return hashlib.sha256(
        json.dumps(doc["plan"], sort_keys=True).encode()
    ).hexdigest()


def solve_fixture(name, seed=None):
    table = load_table(FOODS)
    inst = Instance.load(fixture(name))
    if seed is not None:
        inst.seed = seed
    result = solve(inst, table)
    return report.build(inst, table, result, FOODS)


class Determinism(unittest.TestCase):
    def test_repeated_solves_in_one_process_match(self):
        digests = {plan_digest(solve_fixture("feasible_week.json")) for _ in range(4)}
        self.assertEqual(len(digests), 1, "the same seed produced different plans")

    def test_fresh_processes_match(self):
        # PYTHONHASHSEED varies between runs by default. If anything in the solver
        # iterated a set or dict in hash order, this is where it would show up.
        script = (
            "import json,sys,hashlib;"
            "sys.path.insert(0,'.');"
            "from macrosolver import report;"
            "from macrosolver.foods import load_table;"
            "from macrosolver.model import Instance;"
            "from macrosolver.solver import solve;"
            "t=load_table('data/foods.json');"
            "i=Instance.load('fixtures/feasible_week.json');"
            "d=report.build(i,t,solve(i,t),'data/foods.json');"
            "print(hashlib.sha256(json.dumps(d['plan'],sort_keys=True).encode()).hexdigest())"
        )
        outs = []
        for hashseed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=hashseed)
            r = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                text=True, env=env,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout.strip())
        self.assertEqual(len(set(outs)), 1, f"plans differed between processes: {outs}")

    def test_a_different_seed_is_allowed_to_differ(self):
        # Not a requirement, but if every seed gave the same plan the seed would be
        # decorative and the determinism test above would be proving nothing.
        digests = {plan_digest(solve_fixture("feasible_week.json", seed=s))
                   for s in (1, 2, 3, 4, 5, 6, 7, 8)}
        self.assertGreater(len(digests), 1, "the seed has no effect on the result")

    def test_report_is_stable_byte_for_byte(self):
        a = report.dumps(solve_fixture("feasible_week.json"))
        b = report.dumps(solve_fixture("feasible_week.json"))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
