"""The search.

Honest description of the guarantee, repeated in the README because it matters:

  This is a seeded randomised greedy construction followed by best-improvement
  local search over single-slot replacements. It returns a plan that satisfies
  every constraint, or it says it did not find one. It does NOT prove that the
  plan it returns has the smallest possible macro error, and a `not_found`
  result is NOT a proof that no plan exists.

  Infeasibility is only ever *claimed* when bounds.py produces a certificate,
  which comes from a relaxation and therefore does hold for every plan. Those
  two outcomes are different statuses and the caller can tell them apart.

An integer linear program would be the natural formulation and would give a
proven optimum and a proven infeasibility. No ILP library is installed on this
machine and pip refuses to install into the system Python, so the search here is
written from scratch instead. tests/test_optimality.py measures the gap against
exhaustive enumeration on a small instance so the honesty of the claim above is
checked rather than asserted.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from . import bounds
from .model import EPS, Instance

DEFAULT_MAX_CANDIDATES = 8000
DISTINCT_PENALTY = 25.0


@dataclass(frozen=True)
class Meal:
    """A meal is a set of distinct foods with a serving count each."""

    items: tuple  # sorted tuple of (food_id, servings)

    @property
    def foods(self) -> tuple:
        return tuple(f for f, _ in self.items)

    def to_dict(self) -> dict:
        return {"items": [{"food_id": f, "servings": n} for f, n in self.items]}

    def label(self) -> str:
        return " + ".join(f"{n}x {f}" if n > 1 else f for f, n in self.items)


@dataclass
class SolveResult:
    status: str                      # "solved" | "proven_infeasible" | "not_found"
    plan: list = field(default_factory=list)   # list of Meal, length days*meals_per_day
    certificates: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    @property
    def solved(self) -> bool:
        return self.status == "solved"


def build_candidates(inst: Instance, table, rng: random.Random,
                     max_candidates: int = DEFAULT_MAX_CANDIDATES) -> list:
    """Every meal the structural limits allow, capped at a deterministic sample."""
    pantry = [f for f in inst.pantry if inst.cap_for(f) >= 1]
    pantry.sort()
    serving_range = range(1, inst.max_servings_per_food_in_meal + 1)

    meals = []
    for k in range(1, min(inst.max_foods_per_meal, len(pantry)) + 1):
        for combo in itertools.combinations(pantry, k):
            for counts in itertools.product(serving_range, repeat=k):
                meals.append(Meal(tuple(sorted(zip(combo, counts)))))

    if len(meals) > max_candidates:
        # Keep every single-food meal so the search can always reach a simple
        # plan, then sample the rest deterministically from the seeded rng.
        singles = [m for m in meals if len(m.items) == 1]
        rest = [m for m in meals if len(m.items) > 1]
        keep = max(0, max_candidates - len(singles))
        rest = rng.sample(rest, min(keep, len(rest)))
        meals = singles + sorted(rest, key=lambda m: m.items)
    return meals


class _Search:
    def __init__(self, inst: Instance, table, candidates: list):
        self.inst = inst
        self.table = table
        self.keys = sorted(inst.targets)
        self.cands = candidates
        self.vecs = [
            tuple(
                sum(table[f].amount(k) * n for f, n in m.items)
                for k in self.keys
            )
            for m in candidates
        ]
        self.cand_foods = [m.foods for m in candidates]
        self.scale = [max(abs(inst.targets[k].target), 1.0) for k in self.keys]
        self.modes = [inst.targets[k].mode for k in self.keys]
        self.targets = [inst.targets[k].target for k in self.keys]
        self.lows = [inst.targets[k].low for k in self.keys]
        self.highs = [inst.targets[k].high for k in self.keys]

    # ---- objective -------------------------------------------------------
    def day_error(self, totals) -> float:
        """What the search minimises for one day.

        Two terms. The first is distance outside the accepted band, which is the
        thing that actually decides whether the plan is usable. The second is a
        much smaller pull toward the target itself, which keeps the search moving
        when everything is already inside its band and breaks ties between plans
        that are equally acceptable.
        """
        e = 0.0
        for i, x in enumerate(totals):
            outside = 0.0
            if x < self.lows[i]:
                outside = self.lows[i] - x
            elif x > self.highs[i]:
                outside = x - self.highs[i]
            d = x - self.targets[i]
            if self.modes[i] == "min":
                d = min(d, 0.0)
            elif self.modes[i] == "max":
                d = max(d, 0.0)
            e += (outside + 0.02 * abs(d)) / self.scale[i]
        return e

    def day_totals(self, plan, day):
        s = self.inst.meals_per_day
        out = [0.0] * len(self.keys)
        for idx in range(day * s, day * s + s):
            v = self.vecs[plan[idx]]
            for i in range(len(out)):
                out[i] += v[i]
        return out

    def objective(self, plan) -> float:
        total = sum(self.day_error(self.day_totals(plan, d)) for d in range(self.inst.days))
        distinct = len({f for i in plan for f in self.cand_foods[i]})
        if distinct < self.inst.min_distinct_foods:
            total += DISTINCT_PENALTY * (self.inst.min_distinct_foods - distinct)
        return total

    def hard_ok(self, plan) -> bool:
        usage = {}
        repeats = {}
        for i in plan:
            repeats[i] = repeats.get(i, 0) + 1
            if repeats[i] > self.inst.max_meal_repeats:
                return False
            for f in self.cand_foods[i]:
                usage[f] = usage.get(f, 0) + 1
                if usage[f] > self.inst.cap_for(f):
                    return False
        return True

    def feasible(self, plan) -> bool:
        if not self.hard_ok(plan):
            return False
        distinct = len({f for i in plan for f in self.cand_foods[i]})
        if distinct < self.inst.min_distinct_foods:
            return False
        for d in range(self.inst.days):
            totals = self.day_totals(plan, d)
            for i, k in enumerate(self.keys):
                if not self.inst.targets[k].met(totals[i]):
                    return False
        return True

    # ---- moves -----------------------------------------------------------
    def _allowed(self, cand_idx, slot, plan, usage, repeats):
        cur = plan[slot]
        if cand_idx == cur:
            return True
        if repeats.get(cand_idx, 0) + 1 > self.inst.max_meal_repeats:
            return False
        cur_foods = set(self.cand_foods[cur])
        for f in self.cand_foods[cand_idx]:
            if f in cur_foods:
                continue
            if usage.get(f, 0) + 1 > self.inst.cap_for(f):
                return False
        return True

    def _apply(self, plan, slot, new_idx, usage, repeats):
        old = plan[slot]
        repeats[old] -= 1
        if repeats[old] == 0:
            del repeats[old]
        for f in self.cand_foods[old]:
            usage[f] -= 1
            if usage[f] == 0:
                del usage[f]
        plan[slot] = new_idx
        repeats[new_idx] = repeats.get(new_idx, 0) + 1
        for f in self.cand_foods[new_idx]:
            usage[f] = usage.get(f, 0) + 1

    def _bookkeeping(self, plan):
        usage, repeats = {}, {}
        for i in plan:
            repeats[i] = repeats.get(i, 0) + 1
            for f in self.cand_foods[i]:
                usage[f] = usage.get(f, 0) + 1
        return usage, repeats

    def _fits_empty_slot(self, ci, usage, repeats) -> bool:
        """Whether meal `ci` can be added to a slot that holds nothing yet."""
        if repeats.get(ci, 0) + 1 > self.inst.max_meal_repeats:
            return False
        for f in self.cand_foods[ci]:
            if usage.get(f, 0) + 1 > self.inst.cap_for(f):
                return False
        return True

    def construct(self, rng: random.Random):
        """Randomised greedy: fill slots one at a time, each time aiming the day at
        the share of the target the remaining slots still owe."""
        s = self.inst.meals_per_day
        plan = []
        usage, repeats = {}, {}
        for day in range(self.inst.days):
            running = [0.0] * len(self.keys)
            for slot_in_day in range(s):
                left = s - slot_in_day
                want = [
                    (self.targets[i] - running[i]) / left for i in range(len(self.keys))
                ]
                scored = []
                for ci in range(len(self.cands)):
                    if not self._fits_empty_slot(ci, usage, repeats):
                        continue
                    v = self.vecs[ci]
                    err = sum(
                        abs(v[i] - want[i]) / self.scale[i] for i in range(len(want))
                    )
                    scored.append((err, ci))
                if not scored:
                    return None
                scored.sort()
                top = scored[: max(1, min(8, len(scored)))]
                choice = top[rng.randrange(len(top))][1]
                plan.append(choice)
                repeats[choice] = repeats.get(choice, 0) + 1
                for f in self.cand_foods[choice]:
                    usage[f] = usage.get(f, 0) + 1
                v = self.vecs[choice]
                for i in range(len(running)):
                    running[i] += v[i]
        return plan

    def _distinct_penalty(self, count: int) -> float:
        short = self.inst.min_distinct_foods - count
        return DISTINCT_PENALTY * short if short > 0 else 0.0

    def _distinct_after_swap(self, usage, cur_foods, new_foods) -> int:
        """Distinct-food count if one slot's meal changed, without rebuilding the set.

        `usage` only holds foods with a count above zero, so its size is the
        current distinct count.
        """
        d = len(usage)
        new_set = set(new_foods)
        cur_set = set(cur_foods)
        for f in cur_set - new_set:
            if usage[f] == 1:
                d -= 1
        for f in new_set - cur_set:
            if usage.get(f, 0) == 0:
                d += 1
        return d

    def local_search(self, plan, max_sweeps: int):
        usage, repeats = self._bookkeeping(plan)
        s = self.inst.meals_per_day
        day_err = [self.day_error(self.day_totals(plan, d)) for d in range(self.inst.days)]
        best = sum(day_err) + self._distinct_penalty(len(usage))
        sweeps = 0

        while sweeps < max_sweeps:
            sweeps += 1
            improved = False

            # Move 1: replace the meal in one slot with any other candidate meal.
            for slot in range(len(plan)):
                if self.feasible(plan):
                    return plan, best, sweeps
                day = slot // s
                rest = [0.0] * len(self.keys)
                for idx in range(day * s, day * s + s):
                    if idx == slot:
                        continue
                    v = self.vecs[plan[idx]]
                    for i in range(len(rest)):
                        rest[i] += v[i]
                other_days = best - day_err[day] - self._distinct_penalty(len(usage))

                cur_foods = self.cand_foods[plan[slot]]
                best_idx, best_val, best_err = plan[slot], best, day_err[day]
                for ci in range(len(self.cands)):
                    if not self._allowed(ci, slot, plan, usage, repeats):
                        continue
                    v = self.vecs[ci]
                    err = self.day_error([rest[i] + v[i] for i in range(len(rest))])
                    val = other_days + err + self._distinct_penalty(
                        self._distinct_after_swap(usage, cur_foods, self.cand_foods[ci])
                    )
                    if val < best_val - EPS:
                        best_idx, best_val, best_err = ci, val, err
                if best_idx != plan[slot]:
                    self._apply(plan, slot, best_idx, usage, repeats)
                    day_err[day] = best_err
                    best = best_val
                    improved = True

            # Move 2: exchange the meals sitting in two slots on different days.
            # This never changes food usage or meal repeat counts, so it is always
            # allowed, and it is what fixes a plan whose days are unbalanced once
            # the frequency caps have blocked every single-slot replacement.
            for a in range(len(plan)):
                da = a // s
                for b in range(a + 1, len(plan)):
                    db = b // s
                    if da == db or plan[a] == plan[b]:
                        continue
                    ta = self.day_totals(plan, da)
                    tb = self.day_totals(plan, db)
                    va, vb = self.vecs[plan[a]], self.vecs[plan[b]]
                    na = [ta[i] - va[i] + vb[i] for i in range(len(ta))]
                    nb = [tb[i] - vb[i] + va[i] for i in range(len(tb))]
                    ea, eb = self.day_error(na), self.day_error(nb)
                    delta = (ea + eb) - (day_err[da] + day_err[db])
                    if delta < -EPS:
                        plan[a], plan[b] = plan[b], plan[a]
                        day_err[da], day_err[db] = ea, eb
                        best += delta
                        improved = True
                        if self.feasible(plan):
                            return plan, best, sweeps

            if not improved:
                break
        return plan, best, sweeps


def solve(inst: Instance, table, restarts: int = 4, max_sweeps: int = 10,
          max_candidates: int = DEFAULT_MAX_CANDIDATES) -> SolveResult:
    inst.validate(table.ids())

    certs = bounds.certificates(inst, table)
    if certs:
        return SolveResult(
            status="proven_infeasible",
            certificates=certs,
            notes={
                "why": "a relaxation of this instance already cannot reach the target, "
                       "so no plan exists",
                "headroom": bounds.headroom(inst, table),
            },
        )

    rng = random.Random(inst.seed)
    cands = build_candidates(inst, table, rng, max_candidates)
    if not cands:
        return SolveResult(
            status="proven_infeasible",
            certificates=[
                bounds.Certificate(
                    constraint="pantry:empty",
                    message="no food in the pantry has a frequency cap above zero, "
                            "so no meal can be built at all",
                )
            ],
        )

    search = _Search(inst, table, cands)
    best_plan, best_val = None, float("inf")
    for r in range(restarts):
        rr = random.Random((inst.seed * 1_000_003) + r)
        start = search.construct(rr)
        if start is None:
            continue
        plan, val, _ = search.local_search(start, max_sweeps)
        if search.feasible(plan):
            return SolveResult(
                status="solved",
                plan=[cands[i] for i in plan],
                notes={"restarts_used": r + 1, "objective": val},
            )
        if val < best_val:
            best_plan, best_val = list(plan), val

    notes = {
        "why": "the search did not find a plan meeting every constraint. This is not a "
               "proof that none exists; no bound in bounds.py was violated.",
        "restarts_used": restarts,
        "best_objective": best_val if best_plan else None,
        "headroom": bounds.headroom(inst, table),
    }
    if best_plan is not None:
        notes["closest_misses"] = _misses(search, inst, best_plan)
    return SolveResult(
        status="not_found",
        plan=[cands[i] for i in best_plan] if best_plan else [],
        notes=notes,
    )


def _misses(search, inst, plan) -> list:
    out = []
    for d in range(inst.days):
        totals = search.day_totals(plan, d)
        for i, k in enumerate(search.keys):
            dev = inst.targets[k].deviation(totals[i])
            if dev > 0:
                out.append(
                    {
                        "day": d + 1,
                        "macro": k,
                        "achieved": round(totals[i], 2),
                        "target": inst.targets[k].target,
                        "outside_band_by": round(dev, 2),
                    }
                )
    return out
