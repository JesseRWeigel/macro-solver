"""Data model for macro-solver.

Nothing in here decides what a person should eat. Targets arrive from the caller
already chosen by the caller or copied from their clinician. This module only
records them and the arithmetic that follows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

MACRO_KEYS = ["kcal", "protein_g", "carb_g", "fat_g", "fiber_g", "sodium_mg"]

MACRO_LABELS = {
    "kcal": "energy (kcal)",
    "protein_g": "protein (g)",
    "carb_g": "carbohydrate (g)",
    "fat_g": "fat (g)",
    "fiber_g": "fibre (g)",
    "sodium_mg": "sodium (mg)",
}

# Comparisons on floats derived from the food table need a little slack so that a
# target hit exactly does not fail on a 1e-13 rounding artefact.
EPS = 1e-9


@dataclass(frozen=True)
class Food:
    id: str
    name: str
    serving_description: str
    serving_grams: float
    per_serving: dict
    source: dict

    def amount(self, key: str) -> float:
        return float(self.per_serving.get(key, 0.0))


@dataclass(frozen=True)
class MacroTarget:
    """A number the user supplied, plus how far off it is allowed to land.

    mode:
      band  achieved must sit inside [target - allowance, target + allowance]
      min   achieved must sit at or above target - allowance
      max   achieved must sit at or below target + allowance

    The allowance is the wider of the absolute and percentage tolerances given.
    """

    key: str
    target: float
    tol_abs: float = 0.0
    tol_pct: float = 0.0
    mode: str = "band"

    def __post_init__(self):
        if self.mode not in ("band", "min", "max"):
            raise ValueError(f"unknown target mode {self.mode!r} for {self.key}")
        if self.key not in MACRO_KEYS:
            raise ValueError(f"unknown macro {self.key!r}")
        if self.tol_abs < 0 or self.tol_pct < 0:
            raise ValueError(f"negative tolerance on {self.key}")

    @property
    def allowance(self) -> float:
        return max(self.tol_abs, self.target * self.tol_pct / 100.0)

    @property
    def low(self) -> float:
        """Smallest daily amount that still counts as met."""
        if self.mode == "max":
            return float("-inf")
        return self.target - self.allowance

    @property
    def high(self) -> float:
        """Largest daily amount that still counts as met."""
        if self.mode == "min":
            return float("inf")
        return self.target + self.allowance

    def deviation(self, achieved: float) -> float:
        """How far outside the accepted window, in the macro's own units. 0 when met."""
        if achieved < self.low - EPS:
            return self.low - achieved
        if achieved > self.high + EPS:
            return achieved - self.high
        return 0.0

    def met(self, achieved: float) -> bool:
        return self.deviation(achieved) == 0.0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "target": self.target,
            "tol_abs": self.tol_abs,
            "tol_pct": self.tol_pct,
            "mode": self.mode,
        }


@dataclass
class Instance:
    """One solve request. All targets are per day."""

    pantry: list                       # food ids, order preserved
    targets: dict                      # macro key -> MacroTarget
    days: int = 7
    meals_per_day: int = 3
    max_foods_per_meal: int = 3
    max_servings_per_food_in_meal: int = 2
    frequency_caps: dict = field(default_factory=dict)   # food id -> max meal slots per week
    max_meal_repeats: int = 3          # how often one identical meal may recur
    min_distinct_foods: int = 6        # distinct foods across the whole plan
    seed: int = 0

    @property
    def slots(self) -> int:
        return self.days * self.meals_per_day

    def cap_for(self, food_id: str) -> int:
        """Frequency cap for a food, defaulting to no restriction."""
        return int(self.frequency_caps.get(food_id, self.slots))

    def validate(self, known_ids: Iterable[str]) -> None:
        known = set(known_ids)
        unknown = [f for f in self.pantry if f not in known]
        if unknown:
            raise ValueError(f"pantry names foods not in the food table: {unknown}")
        if len(set(self.pantry)) != len(self.pantry):
            raise ValueError("pantry contains duplicate food ids")
        stray = [f for f in self.frequency_caps if f not in set(self.pantry)]
        if stray:
            raise ValueError(f"frequency caps name foods outside the pantry: {stray}")
        for n, v in (
            ("days", self.days),
            ("meals_per_day", self.meals_per_day),
            ("max_foods_per_meal", self.max_foods_per_meal),
            ("max_servings_per_food_in_meal", self.max_servings_per_food_in_meal),
            ("max_meal_repeats", self.max_meal_repeats),
        ):
            if int(v) < 1:
                raise ValueError(f"{n} must be at least 1, got {v}")
        if self.min_distinct_foods < 0:
            raise ValueError("min_distinct_foods must not be negative")
        if not self.targets:
            raise ValueError("no macro targets given; there is nothing to solve for")

    def to_dict(self) -> dict:
        return {
            "pantry": list(self.pantry),
            "days": self.days,
            "meals_per_day": self.meals_per_day,
            "max_foods_per_meal": self.max_foods_per_meal,
            "max_servings_per_food_in_meal": self.max_servings_per_food_in_meal,
            "targets": {k: t.to_dict() for k, t in self.targets.items()},
            "frequency_caps": dict(self.frequency_caps),
            "max_meal_repeats": self.max_meal_repeats,
            "min_distinct_foods": self.min_distinct_foods,
            "seed": self.seed,
        }

    @staticmethod
    def from_dict(d: dict) -> "Instance":
        targets = {}
        for key, spec in d["targets"].items():
            if isinstance(spec, (int, float)):
                spec = {"target": float(spec)}
            targets[key] = MacroTarget(
                key=key,
                target=float(spec["target"]),
                tol_abs=float(spec.get("tol_abs", 0.0)),
                tol_pct=float(spec.get("tol_pct", 0.0)),
                mode=spec.get("mode", "band"),
            )
        return Instance(
            pantry=list(d["pantry"]),
            targets=targets,
            days=int(d.get("days", 7)),
            meals_per_day=int(d.get("meals_per_day", 3)),
            max_foods_per_meal=int(d.get("max_foods_per_meal", 3)),
            max_servings_per_food_in_meal=int(d.get("max_servings_per_food_in_meal", 2)),
            frequency_caps={k: int(v) for k, v in d.get("frequency_caps", {}).items()},
            max_meal_repeats=int(d.get("max_meal_repeats", 3)),
            min_distinct_foods=int(d.get("min_distinct_foods", 6)),
            seed=int(d.get("seed", 0)),
        )

    @staticmethod
    def load(path: str) -> "Instance":
        with open(path) as fh:
            return Instance.from_dict(json.load(fh))
