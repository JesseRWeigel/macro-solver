"""Every plan the solver hands back goes through the independent checker.

This is the central test. A solver that quietly breaks its own frequency caps or
repeats one meal all week produces output that looks completely normal, and only
a recount from the food table catches it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from helpers import FOODS, checker_module, fixture

from macrosolver import report
from macrosolver.foods import load_table
from macrosolver.model import Instance
from macrosolver.solver import solve

ic = checker_module()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def independent_verdict(doc):
    find = ic.Findings()
    ic.run(doc, FOODS, find)
    return find


class KnownFeasibleInstance(unittest.TestCase):
    def test_the_feasible_fixture_solves_and_survives_the_checker(self):
        table = load_table(FOODS)
        inst = Instance.load(fixture("feasible_week.json"))
        result = solve(inst, table)
        self.assertEqual(result.status, "solved", result.notes)
        doc = report.build(inst, table, result, FOODS)
        find = independent_verdict(doc)
        self.assertEqual(find.problems, [], "\n".join(find.problems))
        self.assertGreater(find.checks, 200)
        self.assertTrue(doc["status_all_targets_met"])

    def test_trivially_feasible_is_not_reported_infeasible(self):
        table = load_table(FOODS)
        inst = Instance.load(fixture("trivially_feasible.json"))
        result = solve(inst, table)
        self.assertEqual(
            result.status, "solved",
            f"an instance with a 60 percent tolerance and no caps came back "
            f"{result.status}: {result.notes}",
        )
        find = independent_verdict(report.build(inst, table, result, FOODS))
        self.assertEqual(find.problems, [], "\n".join(find.problems))


class EverySolutionIsRecounted(unittest.TestCase):
    """Sweep a range of instances and put every returned plan through the checker."""

    def _variants(self):
        with open(fixture("feasible_week.json")) as fh:
            base = json.load(fh)
        for seed in (1, 2, 3, 7, 42):
            spec = json.loads(json.dumps(base))
            spec["seed"] = seed
            yield f"seed {seed}", spec
        for kcal in (1800, 2000, 2400, 2600):
            spec = json.loads(json.dumps(base))
            spec["targets"]["kcal"]["target"] = kcal
            spec["targets"]["protein_g"]["target"] = round(kcal * 0.07)
            spec["targets"]["carb_g"]["target"] = round(kcal * 0.10)
            spec["targets"]["fat_g"]["target"] = round(kcal * 0.032)
            yield f"kcal {kcal}", spec
        for cap in (2, 3, 4):
            spec = json.loads(json.dumps(base))
            spec["frequency_caps"]["chicken_breast"] = cap
            spec["frequency_caps"]["oats"] = cap
            spec["frequency_caps"]["white_rice"] = cap
            yield f"tight caps {cap}", spec
        for repeats in (1, 2):
            spec = json.loads(json.dumps(base))
            spec["max_meal_repeats"] = repeats
            yield f"max repeats {repeats}", spec
        for distinct in (10, 12, 14):
            spec = json.loads(json.dumps(base))
            spec["min_distinct_foods"] = distinct
            yield f"distinct {distinct}", spec
        spec = json.loads(json.dumps(base))
        spec["targets"]["fiber_g"] = {"target": 30, "tol_abs": 5, "mode": "min"}
        yield "fibre floor", spec
        spec = json.loads(json.dumps(base))
        spec["targets"]["sodium_mg"] = {"target": 2000, "tol_abs": 0, "mode": "max"}
        yield "sodium ceiling", spec

    def test_sweep(self):
        table = load_table(FOODS)
        solved = 0
        statuses = {}
        for label, spec in self._variants():
            with self.subTest(instance=label):
                inst = Instance.from_dict(spec)
                result = solve(inst, table)
                statuses[label] = result.status
                if result.status != "solved":
                    # Not a failure. A `not_found` or a certificate is a legitimate
                    # answer; only a returned plan has to survive the recount.
                    continue
                solved += 1
                doc = report.build(inst, table, result, FOODS)
                find = independent_verdict(doc)
                self.assertEqual(
                    find.problems, [],
                    f"{label}: the returned plan breaks its own constraints:\n"
                    + "\n".join(find.problems),
                )
        self.assertGreaterEqual(
            solved, 12,
            f"only {solved} of {len(statuses)} sweep instances solved: {statuses}",
        )


class SolverBookkeepingIsNotTrusted(unittest.TestCase):
    """Sabotage in miniature: if the solver stops enforcing a constraint, does the
    checker still fail? Run inside the test suite so the guarantee is permanent
    rather than a one-off manual attack."""

    def test_ignoring_frequency_caps_is_caught(self):
        import macrosolver.solver as S

        table = load_table(FOODS)
        inst = Instance.load(fixture("feasible_week.json"))
        # Caps the honest solver can satisfy, so any violation below comes from
        # the sabotage rather than from an impossible instance.
        inst.frequency_caps = {
            f: 1
            for f in ("oats", "greek_yogurt", "olive_oil", "almonds", "white_rice", "tofu")
        }
        self.assertEqual(solve(inst, table).status, "solved",
                         "the instance itself must be solvable, or this proves nothing")

        saved = (S._Search._allowed, S._Search._fits_empty_slot, S._Search.hard_ok)
        try:
            S._Search._allowed = lambda self, ci, slot, plan, usage, repeats: True
            S._Search._fits_empty_slot = lambda self, ci, usage, repeats: True
            S._Search.hard_ok = lambda self, plan: True
            result = solve(inst, table)
        finally:
            S._Search._allowed, S._Search._fits_empty_slot, S._Search.hard_ok = saved

        self.assertEqual(result.status, "solved",
                         "sabotage did not even produce a plan, so it proves nothing")
        usage = {}
        for meal in result.plan:
            for food_id, _ in meal.items:
                usage[food_id] = usage.get(food_id, 0) + 1
        self.assertTrue(
            any(n > inst.cap_for(f) for f, n in usage.items()),
            "the sabotaged solver still respected every cap, so the attack was a no-op",
        )
        doc = report.build(inst, table, result, FOODS)
        find = independent_verdict(doc)
        self.assertTrue(
            any("frequency cap broken" in p for p in find.problems),
            f"the checker did not notice the broken caps: {find.problems}",
        )

    def test_same_meal_every_day_is_caught(self):
        table = load_table(FOODS)
        inst = Instance.load(fixture("feasible_week.json"))
        result = solve(inst, table)
        self.assertEqual(result.status, "solved")
        # Overwrite the plan with one meal repeated into every slot.
        result.plan = [result.plan[0]] * len(result.plan)
        doc = report.build(inst, table, result, FOODS)
        find = independent_verdict(doc)
        self.assertTrue(
            any("variety broken" in p for p in find.problems),
            f"the checker did not notice the repeated meal: {find.problems}",
        )


class CliExitCodes(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "macrosolver", *args],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_feasible_exits_zero(self):
        r = self._run(fixture("feasible_week.json"), "--quiet")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_proven_infeasible_exits_one(self):
        r = self._run(fixture("infeasible_protein.json"), "--quiet")
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_malformed_instance_exits_three(self):
        r = self._run(fixture("malformed.json"), "--quiet")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_missing_file_exits_three(self):
        r = self._run(fixture("does-not-exist.json"), "--quiet")
        self.assertEqual(r.returncode, 3)


if __name__ == "__main__":
    unittest.main()
