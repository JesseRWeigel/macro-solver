"""The independent checker is the only thing standing between a broken solver and
a plan that looks fine. So the first thing to establish is that it can fail.

Every test here takes a genuinely valid solved document, breaks exactly one thing
in it, and asserts the checker notices and says what. A checker that passes these
documents would pass anything.
"""

from __future__ import annotations

import copy
import json
import os
import unittest

from helpers import FOODS, checker_module, fixture

from macrosolver import report
from macrosolver.foods import load_table
from macrosolver.model import Instance
from macrosolver.solver import solve

ic = checker_module()


def solved_doc():
    table = load_table(FOODS)
    inst = Instance.load(fixture("feasible_week.json"))
    result = solve(inst, table)
    assert result.status == "solved", result.status
    return report.build(inst, table, result, FOODS)


def check(doc, tmpdir):
    path = os.path.join(tmpdir, "doc.json")
    with open(path, "w") as fh:
        json.dump(doc, fh)
    find = ic.Findings()
    ic.run(doc, FOODS, find)
    return find


class CheckerCanFail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = solved_doc()

    def setUp(self):
        self.doc = copy.deepcopy(self.__class__.doc)

    def assert_flags(self, doc, needle):
        find = ic.Findings()
        ic.run(doc, FOODS, find)
        joined = " || ".join(find.problems)
        self.assertTrue(find.problems, "the checker found nothing wrong")
        self.assertIn(needle, joined)

    def test_untouched_document_passes(self):
        find = ic.Findings()
        ic.run(self.doc, FOODS, find)
        self.assertEqual(find.problems, [], "a genuine solved plan must pass")
        self.assertGreater(find.checks, 100)

    def test_frequency_cap_violation_is_caught(self):
        # Force one capped food into every meal slot of day 1.
        capped = next(iter(self.doc["instance"]["frequency_caps"]))
        self.doc["instance"]["frequency_caps"][capped] = 1
        self.assert_flags(self.doc, "frequency cap broken")

    def test_repeated_meal_is_caught(self):
        first = self.doc["plan"]["days"][0]["meals"][0]["items"]
        for day in self.doc["plan"]["days"]:
            for meal in day["meals"]:
                meal["items"] = copy.deepcopy(first)
        self.assert_flags(self.doc, "variety broken: the meal")

    def test_too_few_distinct_foods_is_caught(self):
        self.doc["instance"]["min_distinct_foods"] = 99
        self.assert_flags(self.doc, "distinct foods")

    def test_macro_total_lie_is_caught(self):
        # The plan stays valid; only the reported number is wrong.
        self.doc["per_day"][0]["macros"]["protein_g"]["achieved"] = 999.0
        self.assert_flags(self.doc, "but the food table gives")

    def test_met_flag_lie_is_caught(self):
        self.doc["per_day"][0]["macros"]["protein_g"]["met"] = False
        self.assert_flags(self.doc, "report says met=False")

    def test_swapping_a_food_out_of_band_is_caught(self):
        # Replace day 1's whole first meal with a single serving of spinach, which
        # drops that day well below every target.
        self.doc["plan"]["days"][0]["meals"][0]["items"] = [
            {"food_id": "spinach", "servings": 1}
        ]
        self.assert_flags(self.doc, "accepted window is")

    def test_serving_count_over_the_limit_is_caught(self):
        self.doc["plan"]["days"][0]["meals"][0]["items"][0]["servings"] = 99
        self.assert_flags(self.doc, "servings, limit is")

    def test_too_many_foods_in_one_meal_is_caught(self):
        meal = self.doc["plan"]["days"][0]["meals"][0]
        meal["items"] = [
            {"food_id": f, "servings": 1}
            for f in self.doc["instance"]["pantry"][:6]
        ]
        self.assert_flags(self.doc, "foods, limit is")

    def test_duplicate_food_in_one_meal_is_caught(self):
        meal = self.doc["plan"]["days"][0]["meals"][0]
        fid = meal["items"][0]["food_id"]
        meal["items"] = [
            {"food_id": fid, "servings": 1},
            {"food_id": fid, "servings": 1},
        ]
        self.assert_flags(self.doc, "more than once")

    def test_food_outside_the_pantry_is_caught(self):
        self.doc["instance"]["pantry"] = [
            f for f in self.doc["instance"]["pantry"] if f != "oats"
        ]
        self.doc["plan"]["days"][0]["meals"][0]["items"] = [
            {"food_id": "oats", "servings": 2}
        ]
        self.assert_flags(self.doc, "is not in the pantry")

    def test_wrong_number_of_days_is_caught(self):
        self.doc["plan"]["days"] = self.doc["plan"]["days"][:3]
        self.assert_flags(self.doc, "days, instance asks for")

    def test_wrong_number_of_meals_is_caught(self):
        self.doc["plan"]["days"][2]["meals"] = self.doc["plan"]["days"][2]["meals"][:1]
        self.assert_flags(self.doc, "meals, instance asks for")

    def test_distinct_food_count_lie_is_caught(self):
        self.doc["distinct_foods"] = 2
        self.assert_flags(self.doc, "distinct foods, recount gives")

    def test_food_usage_count_lie_is_caught(self):
        key = next(iter(self.doc["food_usage"]))
        self.doc["food_usage"][key] = 99
        self.assert_flags(self.doc, "food usage counts do not match")

    def test_infeasible_claim_without_a_certificate_is_caught(self):
        doc = {
            "status": "proven_infeasible",
            "instance": self.doc["instance"],
            "certificates": [],
        }
        self.assert_flags(doc, "no certificate was given")

    def test_allowance_lie_is_caught(self):
        self.doc["per_day"][0]["macros"]["kcal"]["allowance"] = 5000.0
        self.assert_flags(self.doc, "claims allowance")


class CheckerCli(unittest.TestCase):
    def test_expect_flag_rejects_the_wrong_status(self):
        import subprocess
        import sys
        import tempfile

        doc = solved_doc()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "doc.json")
            with open(path, "w") as fh:
                json.dump(doc, fh)
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script = os.path.join(here, "checker", "independent_check.py")
            good = subprocess.run(
                [sys.executable, script, path, "--expect", "solved", "--quiet"]
            )
            bad = subprocess.run(
                [sys.executable, script, path, "--expect", "not_found", "--quiet"],
                capture_output=True,
            )
        self.assertEqual(good.returncode, 0)
        self.assertEqual(bad.returncode, 1)

    def test_unreadable_document_is_exit_two_not_exit_zero(self):
        import subprocess
        import sys
        import tempfile

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(here, "checker", "independent_check.py")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "junk.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            r = subprocess.run([sys.executable, script, path], capture_output=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
