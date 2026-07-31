"""Tolerance classification.

Three cases have to come out right, and the checker has to agree with the solver
about all three: a target hit exactly, one hit inside the tolerance, and one
outside it. The pantry is stripped down to a single food so the achieved numbers
are exact arithmetic rather than whatever the search happens to produce.
"""

from __future__ import annotations

import unittest

from helpers import FOODS, checker_module

from macrosolver.foods import load_table
from macrosolver.model import MacroTarget

ic = checker_module()


class BandArithmetic(unittest.TestCase):
    def test_exact_hit_is_met(self):
        t = MacroTarget("protein_g", 150.0, tol_abs=0.0)
        self.assertTrue(t.met(150.0))
        self.assertEqual(t.deviation(150.0), 0.0)

    def test_zero_tolerance_rejects_anything_off(self):
        t = MacroTarget("protein_g", 150.0, tol_abs=0.0)
        self.assertFalse(t.met(150.5))
        self.assertFalse(t.met(149.5))
        self.assertAlmostEqual(t.deviation(150.5), 0.5)

    def test_inside_the_absolute_tolerance_is_met(self):
        t = MacroTarget("protein_g", 150.0, tol_abs=10.0)
        for v in (140.0, 145.3, 150.0, 159.99, 160.0):
            self.assertTrue(t.met(v), v)

    def test_outside_the_absolute_tolerance_is_not_met(self):
        t = MacroTarget("protein_g", 150.0, tol_abs=10.0)
        self.assertFalse(t.met(160.5))
        self.assertAlmostEqual(t.deviation(160.5), 0.5)
        self.assertFalse(t.met(139.0))
        self.assertAlmostEqual(t.deviation(139.0), 1.0)

    def test_percentage_tolerance(self):
        t = MacroTarget("kcal", 2000.0, tol_pct=5.0)
        self.assertEqual(t.allowance, 100.0)
        self.assertTrue(t.met(1900.0))
        self.assertTrue(t.met(2100.0))
        self.assertFalse(t.met(1899.0))
        self.assertFalse(t.met(2101.0))

    def test_the_wider_of_the_two_tolerances_wins(self):
        t = MacroTarget("kcal", 2000.0, tol_abs=50.0, tol_pct=5.0)
        self.assertEqual(t.allowance, 100.0)
        t2 = MacroTarget("kcal", 2000.0, tol_abs=150.0, tol_pct=5.0)
        self.assertEqual(t2.allowance, 150.0)

    def test_min_mode_ignores_overshoot(self):
        t = MacroTarget("fiber_g", 30.0, tol_abs=2.0, mode="min")
        self.assertTrue(t.met(28.0))
        self.assertTrue(t.met(300.0))
        self.assertFalse(t.met(27.5))
        self.assertAlmostEqual(t.deviation(27.5), 0.5)

    def test_max_mode_ignores_undershoot(self):
        t = MacroTarget("sodium_mg", 2300.0, tol_abs=100.0, mode="max")
        self.assertTrue(t.met(0.0))
        self.assertTrue(t.met(2400.0))
        self.assertFalse(t.met(2400.5))
        self.assertAlmostEqual(t.deviation(2400.5), 0.5)

    def test_bad_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            MacroTarget("kcal", 100.0, mode="approximately")

    def test_negative_tolerance_is_rejected(self):
        with self.assertRaises(ValueError):
            MacroTarget("kcal", 100.0, tol_abs=-1.0)


class CheckerAgreesOnTheSameThreeCases(unittest.TestCase):
    """The independent checker recomputes the window with its own code. If it
    disagreed with the solver about what "met" means, every solve would be
    validated against the wrong bar."""

    def _one_food_doc(self, servings, target, tol_abs, mode="band"):
        table = load_table(FOODS)
        food = table["egg"]
        return {
            "status": "solved",
            "instance": {
                "pantry": ["egg"],
                "days": 1,
                "meals_per_day": 1,
                "max_foods_per_meal": 1,
                "max_servings_per_food_in_meal": servings,
                "targets": {
                    "protein_g": {
                        "target": target,
                        "tol_abs": tol_abs,
                        "tol_pct": 0.0,
                        "mode": mode,
                    }
                },
                "frequency_caps": {},
                "max_meal_repeats": 1,
                "min_distinct_foods": 1,
                "seed": 0,
            },
            "plan": {
                "days": [
                    {
                        "day": 1,
                        "meals": [
                            {"slot": 1, "items": [{"food_id": "egg", "servings": servings}]}
                        ],
                    }
                ]
            },
            "per_day": [
                {
                    "day": 1,
                    "macros": {
                        "protein_g": {
                            "target": target,
                            "achieved": round(food.amount("protein_g") * servings, 3),
                            "allowance": tol_abs,
                            "met": None,   # filled in below
                            "mode": mode,
                        }
                    },
                }
            ],
        }, food.amount("protein_g") * servings

    def _problems(self, doc):
        find = ic.Findings()
        ic.run(doc, FOODS, find)
        return find.problems

    def test_exact_target_passes_the_checker(self):
        doc, achieved = self._one_food_doc(2, target=0.0, tol_abs=0.0)
        doc["instance"]["targets"]["protein_g"]["target"] = achieved
        doc["per_day"][0]["macros"]["protein_g"]["target"] = achieved
        doc["per_day"][0]["macros"]["protein_g"]["met"] = True
        self.assertEqual(self._problems(doc), [])

    def test_within_tolerance_passes_the_checker(self):
        doc, achieved = self._one_food_doc(2, target=0.0, tol_abs=1.0)
        doc["instance"]["targets"]["protein_g"]["target"] = achieved + 0.9
        doc["per_day"][0]["macros"]["protein_g"]["target"] = achieved + 0.9
        doc["per_day"][0]["macros"]["protein_g"]["met"] = True
        self.assertEqual(self._problems(doc), [])

    def test_outside_tolerance_fails_the_checker(self):
        doc, achieved = self._one_food_doc(2, target=0.0, tol_abs=1.0)
        doc["instance"]["targets"]["protein_g"]["target"] = achieved + 5.0
        doc["per_day"][0]["macros"]["protein_g"]["target"] = achieved + 5.0
        doc["per_day"][0]["macros"]["protein_g"]["met"] = True
        problems = self._problems(doc)
        self.assertTrue(problems)
        self.assertIn("outside it by 4.000", " || ".join(problems))


if __name__ == "__main__":
    unittest.main()
