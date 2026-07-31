"""The diagnosis is the most useful thing this tool produces, so it gets checked
harder than anything else.

"No solution" is not an answer. When the instance cannot be satisfied the tool has
to name the constraint and say by how much it misses. Two properties matter:

  * the number quoted has to be right, so it is recomputed here from the food
    table with arithmetic written separately from bounds.py
  * the claim has to be a proof, not a search that gave up, so a search failure
    must come back as `not_found` and never as `proven_infeasible`
"""

from __future__ import annotations

import json
import unittest

from helpers import FOODS, fixture

from macrosolver import report
from macrosolver.foods import load_table
from macrosolver.model import Instance
from macrosolver.solver import solve

with open(FOODS) as _fh:
    RAW = {row["id"]: row["per_serving"] for row in json.load(_fh)["foods"]}


def independent_ceiling(spec, key):
    """Largest weekly total of `key` under the frequency caps and structural
    limits, worked out here rather than read from bounds.py.

    Appearances available: one per food per meal slot, capped at
    max_foods_per_meal per meal. Each appearance carries at most
    max_servings_per_food_in_meal servings. Take the richest foods first.
    """
    slots = spec["days"] * spec["meals_per_day"]
    budget = slots * spec["max_foods_per_meal"]
    servings = spec["max_servings_per_food_in_meal"]
    caps = spec.get("frequency_caps", {})

    amounts = sorted(
        ((float(RAW[f][key]), f) for f in spec["pantry"]), reverse=True
    )
    total = 0.0
    left = budget
    for amount, food in amounts:
        if left <= 0:
            break
        take = min(int(caps.get(food, slots)), left)
        total += take * servings * amount
        left -= take
    return total


class KnownInfeasibleInstance(unittest.TestCase):
    def setUp(self):
        self.table = load_table(FOODS)
        with open(fixture("infeasible_protein.json")) as fh:
            self.spec = json.load(fh)
        self.inst = Instance.load(fixture("infeasible_protein.json"))
        self.result = solve(self.inst, self.table)

    def test_status_is_proven_infeasible(self):
        self.assertEqual(self.result.status, "proven_infeasible")

    def test_protein_is_named_as_the_binding_constraint(self):
        names = [c.constraint for c in self.result.certificates]
        self.assertIn("macro:protein_g:below_target", names)

    def test_the_message_quotes_both_numbers_and_the_gap(self):
        cert = next(
            c for c in self.result.certificates
            if c.constraint == "macro:protein_g:below_target"
        )
        msg = cert.message
        self.assertIn("protein", msg)
        self.assertIn("per day", msg)
        self.assertIn("short by", msg)
        self.assertIn("frequency caps", msg)

    def test_the_quoted_maximum_matches_an_independent_recomputation(self):
        cert = next(
            c for c in self.result.certificates
            if c.constraint == "macro:protein_g:below_target"
        )
        mine = independent_ceiling(self.spec, "protein_g")
        self.assertAlmostEqual(
            cert.detail["max_achievable_per_plan"], mine, places=6,
            msg="the diagnosis quotes a maximum that a separate recomputation "
                "does not agree with",
        )
        self.assertAlmostEqual(
            cert.detail["max_achievable_per_day"], mine / self.spec["days"], places=6
        )
        need = self.inst.targets["protein_g"].low * self.spec["days"]
        self.assertAlmostEqual(
            cert.detail["shortfall_per_day"],
            (need - mine) / self.spec["days"], places=6,
        )
        self.assertGreater(cert.detail["shortfall_per_day"], 0)

    def test_the_report_carries_the_diagnosis(self):
        doc = report.build(self.inst, self.table, self.result, FOODS)
        text = report.render_text(doc)
        self.assertIn("proven", text)
        self.assertIn("protein", text)
        self.assertNotIn("Day 1", text)

    def test_lowering_the_target_below_the_ceiling_removes_the_certificate(self):
        """If the certificate fired for any reason other than the real gap, it
        would keep firing after the gap is closed."""
        ceiling_per_day = independent_ceiling(self.spec, "protein_g") / self.spec["days"]
        spec = json.loads(json.dumps(self.spec))
        spec["targets"]["protein_g"]["target"] = round(ceiling_per_day * 0.4, 1)
        result = solve(Instance.from_dict(spec), self.table)
        self.assertNotEqual(result.status, "proven_infeasible", result.certificates)


class StructuralCertificates(unittest.TestCase):
    def setUp(self):
        self.table = load_table(FOODS)

    def _spec(self, **over):
        spec = {
            "pantry": ["chicken_breast", "white_rice", "broccoli", "olive_oil"],
            "days": 7,
            "meals_per_day": 3,
            "max_foods_per_meal": 3,
            "max_servings_per_food_in_meal": 2,
            "targets": {"kcal": {"target": 2000, "tol_pct": 20}},
            "frequency_caps": {},
            "max_meal_repeats": 3,
            "min_distinct_foods": 3,
            "seed": 1,
        }
        spec.update(over)
        return spec

    def _names(self, spec):
        result = solve(Instance.from_dict(spec), self.table)
        return result.status, [c.constraint for c in result.certificates]

    def test_caps_too_tight_to_fill_the_slots(self):
        spec = self._spec(frequency_caps={
            "chicken_breast": 2, "white_rice": 2, "broccoli": 2, "olive_oil": 2,
        })
        status, names = self._names(spec)
        self.assertEqual(status, "proven_infeasible")
        self.assertIn("frequency_caps:capacity", names)

    def test_not_enough_foods_for_the_variety_floor(self):
        spec = self._spec(min_distinct_foods=9)
        status, names = self._names(spec)
        self.assertEqual(status, "proven_infeasible")
        self.assertIn("variety:min_distinct_foods", names)

    def test_pantry_too_small_for_the_repeat_limit(self):
        spec = self._spec(
            pantry=["broccoli"],
            min_distinct_foods=1,
            max_foods_per_meal=1,
            max_servings_per_food_in_meal=1,
            max_meal_repeats=1,
        )
        status, names = self._names(spec)
        self.assertEqual(status, "proven_infeasible")
        self.assertIn("variety:max_meal_repeats", names)

    def test_a_ceiling_target_below_the_cheapest_possible_plan(self):
        spec = self._spec(targets={
            "kcal": {"target": 50, "tol_abs": 0, "mode": "max"},
        })
        status, names = self._names(spec)
        self.assertEqual(status, "proven_infeasible")
        self.assertIn("macro:kcal:above_target", names)

    def test_a_workable_instance_produces_no_certificate(self):
        status, names = self._names(self._spec())
        self.assertNotEqual(status, "proven_infeasible", names)


class SearchFailureIsNotAProof(unittest.TestCase):
    def test_a_hard_instance_comes_back_not_found_with_headroom(self):
        """Zero tolerance on four macros at once is far beyond what a discrete
        pantry can hit, but nothing proves it, so the honest answer is
        `not_found` and not a certificate."""
        with open(fixture("feasible_week.json")) as fh:
            spec = json.load(fh)
        for key in spec["targets"]:
            spec["targets"][key] = {"target": spec["targets"][key]["target"],
                                    "tol_abs": 0.0}
        inst = Instance.from_dict(spec)
        result = solve(inst, load_table(FOODS))
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.certificates, [])
        self.assertIn("not a proof", result.notes["why"])
        self.assertIn("headroom", result.notes)
        self.assertTrue(result.notes["closest_misses"])
        for miss in result.notes["closest_misses"]:
            self.assertGreater(miss["outside_band_by"], 0)


if __name__ == "__main__":
    unittest.main()
