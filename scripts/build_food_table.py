#!/usr/bin/env python3
"""Build data/foods.json from USDA FoodData Central source rows.

Two stages, so the second one works offline forever:

  --usda-dir DIR   Re-extract the raw rows for the pantry's fdc_ids out of an
                   unpacked SR Legacy CSV distribution, writing
                   data/usda_source_rows.csv and data/usda_portion_rows.csv.
                   Only needed when adding a food or refreshing the dataset.

  (no flag)        Rebuild data/foods.json from the committed source rows.
                   No network, no bulk dataset needed.

Nutrition numbers are never typed by hand. They come out of the USDA rows and
get scaled from per-100-g to the serving size named in data/servings.json,
which is itself a USDA portion row for the same food.

Get the dataset with:
  curl -O https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip
  unzip FoodData_Central_sr_legacy_food_csv_2018-04.zip
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# FoodData Central nutrient ids. Names and units are carried through from the
# dataset's own nutrient.csv when extracting, so these are only a selection.
NUTRIENTS = {
    1008: ("kcal", "KCAL"),
    1003: ("protein_g", "G"),
    1005: ("carb_g", "G"),
    1004: ("fat_g", "G"),
    1079: ("fiber_g", "G"),
    1093: ("sodium_mg", "MG"),
}

MACRO_KEYS = ["kcal", "protein_g", "carb_g", "fat_g", "fiber_g", "sodium_mg"]


def load_servings():
    with open(os.path.join(DATA, "servings.json")) as fh:
        return json.load(fh)


def extract(usda_dir: str, servings: dict) -> None:
    """Pull the rows this pantry depends on out of the bulk CSV distribution."""
    ids = {str(f["fdc_id"]) for f in servings["foods"]}

    with open(os.path.join(usda_dir, "nutrient.csv")) as fh:
        nut_names = {r["id"]: (r["name"], r["unit_name"]) for r in csv.DictReader(fh)}

    with open(os.path.join(usda_dir, "food.csv")) as fh:
        foods = {
            r["fdc_id"]: r
            for r in csv.DictReader(fh)
            if r["fdc_id"] in ids
        }
    missing = ids - set(foods)
    if missing:
        raise SystemExit(f"fdc_ids not present in food.csv: {sorted(missing)}")

    wanted_nut = {str(k) for k in NUTRIENTS}
    rows = []
    with open(os.path.join(usda_dir, "food_nutrient.csv")) as fh:
        for r in csv.DictReader(fh):
            if r["fdc_id"] in ids and r["nutrient_id"] in wanted_nut:
                name, unit = nut_names[r["nutrient_id"]]
                rows.append(
                    {
                        "fdc_id": r["fdc_id"],
                        "description": foods[r["fdc_id"]]["description"],
                        "data_type": foods[r["fdc_id"]]["data_type"],
                        "publication_date": foods[r["fdc_id"]]["publication_date"],
                        "nutrient_id": r["nutrient_id"],
                        "nutrient_name": name,
                        "unit_name": unit,
                        "amount_per_100g": r["amount"],
                    }
                )
    rows.sort(key=lambda r: (int(r["fdc_id"]), int(r["nutrient_id"])))

    out = os.path.join(DATA, "usda_source_rows.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} nutrient rows -> {os.path.relpath(out, ROOT)}")

    with open(os.path.join(usda_dir, "measure_unit.csv")) as fh:
        units = {r["id"]: r["name"] for r in csv.DictReader(fh)}
    prows = []
    with open(os.path.join(usda_dir, "food_portion.csv")) as fh:
        for r in csv.DictReader(fh):
            if r["fdc_id"] in ids:
                prows.append(
                    {
                        "fdc_id": r["fdc_id"],
                        "amount": r["amount"],
                        "measure_unit": units.get(r["measure_unit_id"], ""),
                        "modifier": r["modifier"],
                        "gram_weight": r["gram_weight"],
                    }
                )
    prows.sort(key=lambda r: (int(r["fdc_id"]), float(r["gram_weight"])))
    pout = os.path.join(DATA, "usda_portion_rows.csv")
    with open(pout, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(prows[0].keys()))
        w.writeheader()
        w.writerows(prows)
    print(f"wrote {len(prows)} portion rows -> {os.path.relpath(pout, ROOT)}")


def read_source_rows() -> dict:
    per_food: dict[str, dict] = {}
    with open(os.path.join(DATA, "usda_source_rows.csv")) as fh:
        for r in csv.DictReader(fh):
            e = per_food.setdefault(
                r["fdc_id"],
                {
                    "description": r["description"],
                    "data_type": r["data_type"],
                    "publication_date": r["publication_date"],
                    "nutrients": {},
                },
            )
            key, unit = NUTRIENTS[int(r["nutrient_id"])]
            if r["unit_name"] != unit:
                raise SystemExit(
                    f"unit mismatch for fdc {r['fdc_id']} nutrient {r['nutrient_id']}: "
                    f"expected {unit}, dataset says {r['unit_name']}"
                )
            e["nutrients"][key] = float(r["amount_per_100g"])
    return per_food


def read_portion_rows() -> dict:
    per_food: dict[str, list] = {}
    with open(os.path.join(DATA, "usda_portion_rows.csv")) as fh:
        for r in csv.DictReader(fh):
            per_food.setdefault(r["fdc_id"], []).append(r)
    return per_food


def build(servings: dict) -> dict:
    src = read_source_rows()
    portions = read_portion_rows()
    dataset = servings["dataset"]

    out_foods = []
    for spec in servings["foods"]:
        fid = str(spec["fdc_id"])
        if fid not in src:
            raise SystemExit(f"no source rows for fdc_id {fid}; rerun with --usda-dir")
        entry = src[fid]
        grams = float(spec["grams"])

        matched = [
            p
            for p in portions.get(fid, [])
            if abs(float(p["gram_weight"]) - grams) < 1e-9
        ]
        if not matched:
            raise SystemExit(
                f"serving {grams} g for {spec['id']} is not a USDA portion row; "
                f"available: {[p['gram_weight'] for p in portions.get(fid, [])]}"
            )

        per_serving = {}
        for key in MACRO_KEYS:
            if key not in entry["nutrients"]:
                raise SystemExit(f"{spec['id']} is missing nutrient {key} in the USDA rows")
            per_serving[key] = round(entry["nutrients"][key] * grams / 100.0, 4)

        out_foods.append(
            {
                "id": spec["id"],
                "name": entry["description"],
                "serving": {"description": spec["portion"], "grams": grams},
                "per_serving": per_serving,
                "per_100g": {k: entry["nutrients"][k] for k in MACRO_KEYS},
                "source": {
                    "database": dataset["name"],
                    "fdc_id": int(fid),
                    "data_type": entry["data_type"],
                    "publication_date": entry["publication_date"],
                    "url": dataset["food_detail_url_pattern"].format(fdc_id=fid),
                    "serving_from": "USDA food_portion row: "
                    + f"{spec['portion']} = {grams} g",
                },
            }
        )

    return {
        "dataset": dataset,
        "units": {
            "kcal": "kcal",
            "protein_g": "g",
            "carb_g": "g",
            "fat_g": "g",
            "fiber_g": "g",
            "sodium_mg": "mg",
        },
        "foods": out_foods,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--usda-dir", help="unpacked SR Legacy CSV directory")
    ap.add_argument("--check", action="store_true",
                    help="rebuild and compare against data/foods.json without writing")
    args = ap.parse_args(argv)

    servings = load_servings()
    if args.usda_dir:
        extract(args.usda_dir, servings)

    table = build(servings)
    text = json.dumps(table, indent=2, sort_keys=False) + "\n"
    path = os.path.join(DATA, "foods.json")

    if args.check:
        if not os.path.exists(path):
            print("data/foods.json is missing", file=sys.stderr)
            return 1
        with open(path) as fh:
            current = fh.read()
        if current != text:
            print("data/foods.json does not match a rebuild from the USDA rows",
                  file=sys.stderr)
            return 1
        print(f"data/foods.json matches a rebuild from the USDA rows "
              f"({len(table['foods'])} foods)")
        return 0

    with open(path, "w") as fh:
        fh.write(text)
    print(f"wrote {len(table['foods'])} foods -> data/foods.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
