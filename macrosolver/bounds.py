"""Proven bounds and infeasibility certificates.

"No solution" on its own is close to useless, and it is also ambiguous: it can
mean "I proved nothing works" or "my search gave up". This module only ever
produces the first kind. Every certificate here comes from a relaxation of the
real problem, so if the relaxation cannot reach the target then neither can any
real plan. The search in solver.py never produces a certificate, only a report
that it did not find anything.

The relaxations:

  occupancy   One meal holds at most `max_foods_per_meal` distinct foods, so the
              whole plan has at most days * meals_per_day * max_foods_per_meal
              food-in-meal appearances. Food f may appear in at most cap_f meal
              slots, and a meal holds a food at most once, so f uses at most
              cap_f of those appearances.

  macro max   Each appearance of f contributes at most max_servings * amount_f of
              a macro. Maximising over appearance counts subject to the two
              limits above is a fractional knapsack with unit weights, so sorting
              by amount and taking greedily is exactly optimal. Day-by-day
              balance is dropped, which can only make the bound larger.

  macro min   Every meal slot holds at least one food and at least one serving,
              so the plan has at least days * meals_per_day appearances, each
              contributing at least amount_f. All macro amounts are
              non-negative, so the minimum sits at exactly that many
              appearances, taking the smallest amounts first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import EPS, MACRO_LABELS, Instance


@dataclass
class Certificate:
    """A proof that no plan can satisfy the instance, and by how much it misses."""

    constraint: str
    message: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"constraint": self.constraint, "message": self.message, "detail": self.detail}


def _fmt(x: float) -> str:
    return f"{x:,.1f}"


def occupancy_budget(inst: Instance) -> int:
    """Most food-in-meal appearances any plan can use."""
    return inst.slots * inst.max_foods_per_meal


def _greedy_extreme(inst, table, key, maximise):
    """Exact optimum of the relaxed appearance problem, with the picks that made it."""
    if maximise:
        budget = occupancy_budget(inst)
        per_appearance = inst.max_servings_per_food_in_meal
        order = sorted(
            inst.pantry, key=lambda f: (-table[f].amount(key), f)
        )
    else:
        # A minimum only has to cover one food and one serving per meal slot.
        budget = inst.slots
        per_appearance = 1
        order = sorted(inst.pantry, key=lambda f: (table[f].amount(key), f))

    total = 0.0
    remaining = budget
    picks = []
    for food_id in order:
        if remaining <= 0:
            break
        take = min(inst.cap_for(food_id), remaining)
        if take <= 0:
            continue
        contribution = take * per_appearance * table[food_id].amount(key)
        total += contribution
        picks.append(
            {
                "food": food_id,
                "appearances": take,
                "servings_each": per_appearance,
                "contributes": round(contribution, 4),
            }
        )
        remaining -= take
    return total, picks, remaining


def macro_ceiling(inst: Instance, table, key: str):
    """Largest weekly total of `key` any plan can reach. Exact for the relaxation."""
    total, picks, _ = _greedy_extreme(inst, table, key, maximise=True)
    return total, picks


def macro_floor(inst: Instance, table, key: str):
    """Smallest weekly total of `key` any plan can reach. Exact for the relaxation."""
    total, picks, _ = _greedy_extreme(inst, table, key, maximise=False)
    return total, picks


def distinct_meal_count(inst: Instance) -> int:
    """How many distinct meals the pantry can express under the structural limits."""
    n = len(inst.pantry)
    v = inst.max_servings_per_food_in_meal
    total = 0
    for k in range(1, min(inst.max_foods_per_meal, n) + 1):
        total += math.comb(n, k) * (v ** k)
    return total


def certificates(inst: Instance, table) -> list:
    """Every proof of infeasibility that holds for this instance. Empty means
    nothing was proven, which is not the same as feasible."""
    out = []

    usable = [f for f in inst.pantry if inst.cap_for(f) >= 1]
    total_appearances = sum(min(inst.cap_for(f), inst.slots) for f in inst.pantry)

    if total_appearances < inst.slots:
        out.append(
            Certificate(
                constraint="frequency_caps:capacity",
                message=(
                    f"the plan has {inst.slots} meal slots and each one needs at least one "
                    f"food, but the frequency caps allow only {total_appearances} food "
                    f"appearances in total across the week, short by "
                    f"{inst.slots - total_appearances}"
                ),
                detail={
                    "slots": inst.slots,
                    "appearances_allowed": total_appearances,
                    "shortfall": inst.slots - total_appearances,
                },
            )
        )

    if len(usable) < inst.min_distinct_foods:
        out.append(
            Certificate(
                constraint="variety:min_distinct_foods",
                message=(
                    f"the plan must use at least {inst.min_distinct_foods} distinct foods, "
                    f"but only {len(usable)} foods in the pantry have a frequency cap above "
                    f"zero, short by {inst.min_distinct_foods - len(usable)}"
                ),
                detail={
                    "required": inst.min_distinct_foods,
                    "available": len(usable),
                    "shortfall": inst.min_distinct_foods - len(usable),
                },
            )
        )

    max_distinct_by_slots = occupancy_budget(inst)
    if inst.min_distinct_foods > max_distinct_by_slots:
        out.append(
            Certificate(
                constraint="variety:min_distinct_foods_vs_slots",
                message=(
                    f"the plan must use at least {inst.min_distinct_foods} distinct foods, but "
                    f"{inst.slots} meal slots holding at most {inst.max_foods_per_meal} foods "
                    f"each leave room for only {max_distinct_by_slots} food appearances"
                ),
                detail={
                    "required": inst.min_distinct_foods,
                    "appearance_budget": max_distinct_by_slots,
                },
            )
        )

    needed_meals = math.ceil(inst.slots / inst.max_meal_repeats)
    possible_meals = distinct_meal_count(inst)
    if possible_meals < needed_meals:
        out.append(
            Certificate(
                constraint="variety:max_meal_repeats",
                message=(
                    f"no meal may repeat more than {inst.max_meal_repeats} times, so "
                    f"{inst.slots} slots need at least {needed_meals} distinct meals, but this "
                    f"pantry can only express {possible_meals}"
                ),
                detail={
                    "distinct_meals_needed": needed_meals,
                    "distinct_meals_possible": possible_meals,
                },
            )
        )

    for key, target in inst.targets.items():
        label = MACRO_LABELS.get(key, key)
        if target.low != float("-inf"):
            ceiling, picks = macro_ceiling(inst, table, key)
            need_week = target.low * inst.days
            if ceiling < need_week - EPS:
                out.append(
                    Certificate(
                        constraint=f"macro:{key}:below_target",
                        message=(
                            f"{label} target needs at least {_fmt(target.low)} per day "
                            f"({_fmt(need_week)} across {inst.days} days), but the pantry's "
                            f"maximum under the frequency caps and the "
                            f"{inst.max_foods_per_meal} foods per meal limit is "
                            f"{_fmt(ceiling / inst.days)} per day ({_fmt(ceiling)} across "
                            f"{inst.days} days), short by "
                            f"{_fmt((need_week - ceiling) / inst.days)} per day"
                        ),
                        detail={
                            "macro": key,
                            "required_per_day": target.low,
                            "max_achievable_per_day": ceiling / inst.days,
                            "shortfall_per_day": (need_week - ceiling) / inst.days,
                            "required_per_plan": need_week,
                            "max_achievable_per_plan": ceiling,
                            "binding_picks": picks,
                        },
                    )
                )
        if target.high != float("inf"):
            floor_, picks = macro_floor(inst, table, key)
            allow_week = target.high * inst.days
            if floor_ > allow_week + EPS:
                out.append(
                    Certificate(
                        constraint=f"macro:{key}:above_target",
                        message=(
                            f"{label} target allows at most {_fmt(target.high)} per day "
                            f"({_fmt(allow_week)} across {inst.days} days), but every meal slot "
                            f"needs at least one food, so the smallest total this pantry can "
                            f"produce is {_fmt(floor_ / inst.days)} per day ({_fmt(floor_)} "
                            f"across {inst.days} days), over by "
                            f"{_fmt((floor_ - allow_week) / inst.days)} per day"
                        ),
                        detail={
                            "macro": key,
                            "allowed_per_day": target.high,
                            "min_achievable_per_day": floor_ / inst.days,
                            "excess_per_day": (floor_ - allow_week) / inst.days,
                            "allowed_per_plan": allow_week,
                            "min_achievable_per_plan": floor_,
                            "binding_picks": picks,
                        },
                    )
                )

    return out


def headroom(inst: Instance, table) -> dict:
    """Per-macro proven window, for reporting next to a search that came up empty."""
    out = {}
    for key in inst.targets:
        ceiling, _ = macro_ceiling(inst, table, key)
        floor_, _ = macro_floor(inst, table, key)
        out[key] = {
            "min_achievable_per_day": floor_ / inst.days,
            "max_achievable_per_day": ceiling / inst.days,
            "target_low_per_day": inst.targets[key].low,
            "target_high_per_day": inst.targets[key].high,
        }
    return out
