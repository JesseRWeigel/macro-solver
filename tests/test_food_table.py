"""The food table has to trace back to USDA rows, with nothing typed in by hand.

Two independent routes to the same numbers:

  1. scripts/build_food_table.py --check rebuilds data/foods.json from
     data/usda_source_rows.csv and compares byte for byte.
  2. this file recomputes every per-serving figure straight from the CSV with
     arithmetic written here, so a bug in the build script cannot hide behind its
     own rebuild.

The CSV rows themselves carry the fdc_id, the USDA description, the data type and
the publication date, so any figure can be looked up at
https://fdc.nal.usda.gov/food-details/<fdc_id>/nutrients.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import unittest

from helpers import FOODS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

NUTRIENT_IDS = {
    "1008": "kcal",
    "1003": "protein_g",
    "1005": "carb_g",
    "1004": "fat_g",
    "1079": "fiber_g",
    "1093": "sodium_mg",
}


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


class TableMatchesTheUsdaRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = load_json(FOODS)
        cls.servings = load_json(os.path.join(DATA, "servings.json"))
        with open(os.path.join(DATA, "usda_source_rows.csv")) as fh:
            cls.rows = list(csv.DictReader(fh))
        with open(os.path.join(DATA, "usda_portion_rows.csv")) as fh:
            cls.portions = list(csv.DictReader(fh))

    def test_every_per_serving_number_is_the_usda_value_scaled_by_grams(self):
        by_fdc = {}
        for row in self.rows:
            by_fdc.setdefault(row["fdc_id"], {})[
                NUTRIENT_IDS[row["nutrient_id"]]
            ] = float(row["amount_per_100g"])

        checked = 0
        for food in self.table["foods"]:
            fdc = str(food["source"]["fdc_id"])
            grams = float(food["serving"]["grams"])
            self.assertIn(fdc, by_fdc, f"{food['id']} has no USDA rows")
            for key, value in food["per_serving"].items():
                expected = by_fdc[fdc][key] * grams / 100.0
                self.assertAlmostEqual(
                    value, expected, places=3,
                    msg=f"{food['id']} {key}: table says {value}, USDA row scaled "
                        f"to {grams} g gives {expected}",
                )
                checked += 1
        self.assertGreaterEqual(checked, 6 * len(self.table["foods"]))

    def test_every_serving_size_is_a_usda_portion_row(self):
        by_fdc = {}
        for row in self.portions:
            by_fdc.setdefault(row["fdc_id"], set()).add(round(float(row["gram_weight"]), 4))
        for food in self.table["foods"]:
            fdc = str(food["source"]["fdc_id"])
            grams = round(float(food["serving"]["grams"]), 4)
            self.assertIn(
                grams, by_fdc.get(fdc, set()),
                f"{food['id']} claims a {grams} g serving, which is not a USDA "
                f"portion row for fdc {fdc}",
            )

    def test_every_food_cites_a_source(self):
        for food in self.table["foods"]:
            src = food["source"]
            self.assertTrue(src.get("fdc_id"))
            self.assertIn("fdc.nal.usda.gov", src["url"])
            self.assertIn(str(src["fdc_id"]), src["url"])
            self.assertIn("fdc.nal.usda.gov", src["portal_url"])
            self.assertIn(str(src["fdc_id"]), src["portal_url"])
            self.assertTrue(src.get("data_type"))
            self.assertTrue(src.get("publication_date"))
            self.assertEqual(src["database"], "USDA FoodData Central, SR Legacy")

    def test_names_are_the_usda_descriptions(self):
        by_fdc = {row["fdc_id"]: row["description"] for row in self.rows}
        for food in self.table["foods"]:
            self.assertEqual(food["name"], by_fdc[str(food["source"]["fdc_id"])])

    def test_no_food_has_an_all_zero_row(self):
        # An entry where every macro is zero would mean the extraction silently
        # dropped the food rather than that the food has no calories.
        for food in self.table["foods"]:
            self.assertGreater(
                sum(food["per_serving"].values()), 0.0,
                f"{food['id']} has no nutrition at all, which means the row is missing",
            )

    def test_the_starter_set_is_small_and_says_so(self):
        self.assertLessEqual(len(self.table["foods"]), 30)
        self.assertGreaterEqual(len(self.table["foods"]), 10)

    def test_dataset_provenance_is_recorded(self):
        ds = self.table["dataset"]
        self.assertIn("fdc.nal.usda.gov", ds["url"])
        self.assertEqual(len(ds["zip_sha256"]), 64)
        self.assertTrue(ds["retrieved"])


class RebuildIsReproducible(unittest.TestCase):
    def test_build_script_check_mode_passes(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "build_food_table.py"), "--check"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("matches a rebuild", r.stdout)

    def test_check_mode_notices_a_tampered_table(self):
        import shutil
        import tempfile

        path = os.path.join(DATA, "foods.json")
        with tempfile.TemporaryDirectory() as td:
            backup = os.path.join(td, "foods.json")
            shutil.copy(path, backup)
            try:
                table = load_json(path)
                table["foods"][0]["per_serving"]["protein_g"] = 999.0
                with open(path, "w") as fh:
                    json.dump(table, fh, indent=2)
                    fh.write("\n")
                r = subprocess.run(
                    [sys.executable,
                     os.path.join(ROOT, "scripts", "build_food_table.py"), "--check"],
                    capture_output=True, text=True, cwd=ROOT,
                )
            finally:
                shutil.copy(backup, path)
        self.assertEqual(r.returncode, 1, "a hand-edited nutrition number went unnoticed")


if __name__ == "__main__":
    unittest.main()
