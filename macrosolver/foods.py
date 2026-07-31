"""Loading the food table.

Every number here came out of data/foods.json, which scripts/build_food_table.py
derives from USDA FoodData Central rows. Nothing in this module invents a value
or fills a missing one in with a guess.
"""

from __future__ import annotations

import json
import os

from .model import MACRO_KEYS, Food

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TABLE = os.path.join(ROOT, "data", "foods.json")


class FoodTable:
    def __init__(self, foods: dict, dataset: dict):
        self.foods = foods
        self.dataset = dataset

    def __contains__(self, food_id: str) -> bool:
        return food_id in self.foods

    def __getitem__(self, food_id: str) -> Food:
        return self.foods[food_id]

    def __len__(self) -> int:
        return len(self.foods)

    def ids(self):
        return list(self.foods)

    def values(self):
        return list(self.foods.values())


def load_table(path: str = DEFAULT_TABLE) -> FoodTable:
    with open(path) as fh:
        raw = json.load(fh)

    foods = {}
    for entry in raw["foods"]:
        missing = [k for k in MACRO_KEYS if k not in entry["per_serving"]]
        if missing:
            # A missing nutrient is not a zero. Refuse the table rather than
            # silently solving against numbers that are not there.
            raise ValueError(
                f"food {entry['id']!r} has no value for {missing}; "
                "the food table must carry every macro it claims to cover"
            )
        if not entry.get("source", {}).get("fdc_id"):
            raise ValueError(f"food {entry['id']!r} carries no source citation")
        foods[entry["id"]] = Food(
            id=entry["id"],
            name=entry["name"],
            serving_description=entry["serving"]["description"],
            serving_grams=float(entry["serving"]["grams"]),
            per_serving={k: float(entry["per_serving"][k]) for k in MACRO_KEYS},
            source=entry["source"],
        )
    return FoodTable(foods, raw.get("dataset", {}))
