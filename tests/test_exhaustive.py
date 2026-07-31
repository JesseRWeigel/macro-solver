"""Ground truth by brute force, on instances small enough to enumerate completely.

The solver is a heuristic. The only way to say anything solid about it is to pick
an instance where every plan can be listed, work out the true answer, and compare.
Two things get checked here:

  1. Soundness of the infeasibility certificates. If brute force finds a plan and
     the solver says `proven_infeasible`, the certificate is a lie and that is the
     worst possible bug in this tool. This test would catch it.

  2. Soundness of the bounds in bounds.py. The claimed maximum and minimum for a
     macro must actually bracket the true maximum and minimum over all plans.

The enumerator here does not import macrosolver. It builds plans from the raw
food table and evaluates them with the independent checker's window arithmetic.
"""

from __future__ import annotations

import itertools
import json
import unittest

from helpers import FOODS, checker_module

from macrosolver import bounds
from macrosolver.foods import load_table
from macrosolver.model import Instance
from macrosolver.solver import solve

ic = checker_module()

with open(FOODS) as _fh:
    RAW = {row["id"]: row["per_serving"] for row in json.load(_fh)["foods"]}


def enumerate_meals(pantry, max_foods, max_servings):
    meals = []
    for k in range(1, min(max_foods, len(pantry)) + 1):
        for combo in itertools.combinations(sorted(pantry), k):
            for counts in itertools.product(range(1, max_servings + 1), repeat=k):
                meals.append(tuple(sorted(zip(combo, counts))))
    return meals


def plan_is_feasible(spec, plan):
    """Independent evaluation of one complete plan. Returns True when every
    constraint in `spec` holds."""
    days, per_day = spec["days"], spec["meals_per_day"]
    caps = spec.get("frequency_caps", {})
    default_cap = days * per_day

    usage, repeats, distinct = {}, {}, set()
    for meal in plan:
        repeats[meal] = repeats.get(meal, 0) + 1
        if repeats[meal] > spec["max_meal_repeats"]:
            return False
        for food, _ in meal:
            usage[food] = usage.get(food, 0) + 1
            distinct.add(food)
            if usage[food] > int(caps.get(food, default_cap)):
                return False
    if len(distinct) < spec["min_distinct_foods"]:
        return False

    for d in range(days):
        totals = {}
        for meal in plan[d * per_day : (d + 1) * per_day]:
            for food, n in meal:
                for macro, amount in RAW[food].items():
                    totals[macro] = totals.get(macro, 0.0) + float(amount) * n
        for key, tspec in spec["targets"].items():
            lo, hi, _ = ic.accepted_window(tspec)
            if ic.miss_amount(totals.get(key, 0.0), lo, hi) != 0.0:
                return False
    return True


def any_feasible(spec):
    meals = enumerate_meals(
        spec["pantry"], spec["max_foods_per_meal"], spec["max_servings_per_food_in_meal"]
    )
    slots = spec["days"] * spec["meals_per_day"]
    for plan in itertools.product(meals, repeat=slots):
        if plan_is_feasible(spec, plan):
            return plan
    return None


def macro_extremes(spec, key):
    """True maximum and minimum weekly total of `key` over every legal plan,
    ignoring the macro targets themselves."""
    relaxed = dict(spec)
    relaxed["targets"] = {}
    meals = enumerate_meals(
        spec["pantry"], spec["max_foods_per_meal"], spec["max_servings_per_food_in_meal"]
    )
    slots = spec["days"] * spec["meals_per_day"]
    lo, hi = None, None
    for plan in itertools.product(meals, repeat=slots):
        if not plan_is_feasible(relaxed, plan):
            continue
        total = sum(
            float(RAW[food][key]) * n for meal in plan for food, n in meal
        )
        lo = total if lo is None else min(lo, total)
        hi = total if hi is None else max(hi, total)
    return lo, hi


BASE = {
    "pantry": ["egg", "white_rice", "broccoli", "olive_oil"],
    "days": 1,
    "meals_per_day": 2,
    "max_foods_per_meal": 2,
    "max_servings_per_food_in_meal": 2,
    "targets": {},
    "frequency_caps": {},
    "max_meal_repeats": 1,
    "min_distinct_foods": 2,
    "seed": 5,
}

WEEKLY_CAPS = {
    "pantry": ["egg", "white_rice", "broccoli", "olive_oil"],
    "days": 2,
    "meals_per_day": 1,
    "max_foods_per_meal": 1,
    "max_servings_per_food_in_meal": 2,
    "targets": {},
    "frequency_caps": {"egg": 1, "white_rice": 1},
    "max_meal_repeats": 1,
    "min_distinct_foods": 2,
    "seed": 5,
}


class SolverAgainstBruteForce(unittest.TestCase):
    def _sweep(self, base, targets_list):
        table = load_table(FOODS)
        agreed, missed = 0, []
        for targets in targets_list:
            spec = dict(base)
            spec["targets"] = targets
            truth = any_feasible(spec)
            result = solve(Instance.from_dict(spec), table, restarts=6, max_sweeps=20)

            if truth is not None:
                self.assertNotEqual(
                    result.status,
                    "proven_infeasible",
                    f"certificate claims no plan exists but brute force found one "
                    f"for {targets}: {truth}",
                )
                if result.status == "solved":
                    agreed += 1
                else:
                    missed.append(targets)
            else:
                self.assertNotEqual(
                    result.status,
                    "solved",
                    f"solver returned a plan for {targets} but brute force says "
                    f"none satisfies the constraints",
                )
        return agreed, missed

    def test_single_day_sweep(self):
        targets = [
            {"kcal": {"target": k, "tol_abs": 25}} for k in range(150, 1500, 75)
        ]
        agreed, missed = self._sweep(BASE, targets)
        self.assertGreater(agreed, 0)
        # The search is a heuristic, so a miss on a feasible instance is allowed.
        # It is recorded here so a regression that makes it worse is visible.
        self.assertLessEqual(
            len(missed), 2,
            f"the search failed on {len(missed)} feasible small instances: {missed}",
        )

    def test_weekly_frequency_caps_sweep(self):
        targets = [
            {"kcal": {"target": k, "tol_abs": 40}} for k in range(50, 800, 40)
        ]
        agreed, missed = self._sweep(WEEKLY_CAPS, targets)
        self.assertGreater(agreed, 0)
        self.assertLessEqual(len(missed), 2, f"search failed on {missed}")

    def test_impossible_protein_on_a_tiny_instance(self):
        spec = dict(BASE)
        spec["targets"] = {"protein_g": {"target": 500, "tol_abs": 1, "mode": "min"}}
        self.assertIsNone(any_feasible(spec))
        result = solve(Instance.from_dict(spec), load_table(FOODS))
        self.assertEqual(result.status, "proven_infeasible")
        self.assertTrue(
            any(c.constraint == "macro:protein_g:below_target" for c in result.certificates)
        )


class BoundsBracketTheTruth(unittest.TestCase):
    def _check(self, spec, key):
        table = load_table(FOODS)
        inst = Instance.from_dict(dict(spec, targets={"kcal": {"target": 1}}))
        true_min, true_max = macro_extremes(spec, key)
        ceiling, _ = bounds.macro_ceiling(inst, table, key)
        floor_, _ = bounds.macro_floor(inst, table, key)
        self.assertIsNotNone(true_max)
        self.assertGreaterEqual(
            ceiling + 1e-9, true_max,
            f"claimed maximum {ceiling} for {key} is below the real maximum {true_max}",
        )
        self.assertLessEqual(
            floor_ - 1e-9, true_min,
            f"claimed minimum {floor_} for {key} is above the real minimum {true_min}",
        )
        return floor_, true_min, true_max, ceiling

    def test_bounds_bracket_kcal_and_protein(self):
        for key in ("kcal", "protein_g", "fat_g"):
            with self.subTest(key=key):
                self._check(BASE, key)

    def test_bounds_hold_with_frequency_caps(self):
        for key in ("kcal", "protein_g"):
            with self.subTest(key=key):
                self._check(WEEKLY_CAPS, key)


if __name__ == "__main__":
    unittest.main()
