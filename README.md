# macro-solver

Fit a week of meals to macro and micronutrient targets **you** supply, from a pantry
**you** list, respecting per-food frequency caps and a variety constraint. When the
targets cannot be met, say which constraint could not be satisfied and by how much.

## What this is, and what it is not

You give this tool a set of daily targets. It arranges servings of the foods you say
you have into a week of meals whose daily totals land inside those targets.

The targets come from you, or you copy them from your clinician. This tool does not
choose them, does not suggest them, and has no view on whether they suit you or
anyone. There is no "recommended intake" feature and there will not be one. It is a
constraint solver over numbers someone else chose, in the same sense that a
calculator is arithmetic over numbers someone else chose.
**This is not nutrition advice and not medical advice.** It does not check whether
your targets are appropriate, safe, or even survivable.

Food composition data is reference data. Real chicken breast is not USDA entry
171477. Treat every total the tool prints as an estimate.

## Running it

```
python3 -m macrosolver fixtures/feasible_week.json
python3 -m macrosolver fixtures/feasible_week.json --json --out plan.json
python3 checker/independent_check.py plan.json --expect solved
bash scripts/verify.sh
```

There are no dependencies beyond the Python 3 standard library. `docs/index.html`
does the same thing in a browser with no server, no build step and no network.

Exit codes: `0` solved, `1` proven infeasible, `2` no plan found and nothing proven,
`3` the request was malformed.

## The instance format

```json
{
  "pantry": ["chicken_breast", "white_rice", "broccoli"],
  "days": 7,
  "meals_per_day": 3,
  "max_foods_per_meal": 3,
  "max_servings_per_food_in_meal": 2,
  "targets": {
    "kcal":      {"target": 2150, "tol_pct": 5},
    "protein_g": {"target": 150,  "tol_abs": 12},
    "fiber_g":   {"target": 30,   "tol_abs": 3, "mode": "min"},
    "sodium_mg": {"target": 2300, "tol_abs": 0, "mode": "max"}
  },
  "frequency_caps": {"chicken_breast": 3},
  "max_meal_repeats": 3,
  "min_distinct_foods": 8,
  "seed": 7
}
```

* Targets are **per day**. The allowance is the wider of `tol_abs` and `tol_pct`.
* `mode` is `band` (within tolerance either way), `min` (at least) or `max` (at most).
* A frequency cap is the number of meal slots in the whole plan a food may appear in.
  "Chicken at most 3 times a week" is `{"chicken_breast": 3}`. Unlisted foods are
  uncapped.
* `max_meal_repeats` limits how often one identical meal composition may recur across
  the plan. `min_distinct_foods` is the variety floor across the whole plan.
* The same seed and the same inputs always produce the same plan.

## How it solves, and what that guarantee is worth

An integer linear program is the natural formulation for this, and it would give a
proven optimum and a proven infeasibility for free. No ILP library is installed on
this machine and pip refuses to install into the system Python, so the search here is
written from scratch:

1. Enumerate every meal the structural limits allow, which is every subset of the
   pantry up to `max_foods_per_meal` foods crossed with every serving count up to
   `max_servings_per_food_in_meal`. For the 16-food starter pantry with 3 foods and 2
   servings that is 4,992 candidate meals.
2. Build a starting plan with a seeded randomised greedy: fill slots one at a time,
   each time aiming that day at the share of the target the remaining slots still owe,
   choosing among the eight best fits.
3. Improve it with best-improvement local search over two moves: replace the meal in
   one slot with any other candidate, and exchange the meals in two slots on different
   days. The second move never changes food usage or repeat counts, which is what
   rescues a plan whose days are unbalanced after the frequency caps have blocked
   every single-slot replacement.
4. Repeat from four different starting points, stopping the moment a plan satisfies
   everything.

Frequency caps and the meal repeat limit are enforced by construction, so the search
never visits a state that breaks them. Macro bands and the distinct-food floor are in
the objective, and the result is only reported as solved once all of them hold.

**The honest guarantee.** This finds a good plan. It does **not** prove that plan has
the smallest achievable distance to your targets, and it is **not proven** optimal.
Nothing in this repository claims otherwise. On instances small enough to enumerate
completely, `tests/test_exhaustive.py` compares against brute force over every legal
plan: across a 18-point target sweep on a one-day instance and a 19-point sweep on a
two-day instance with binding frequency caps, the search found a plan on every
instance brute force says is feasible, and returned no plan on every instance brute
force says is not. That is a measurement on small instances, not a proof about large
ones.

**Infeasibility is different, and it is proven.** The three statuses are distinct and
the caller can tell them apart:

| status | meaning |
|---|---|
| `solved` | a plan was found and every constraint holds |
| `proven_infeasible` | a relaxation of the instance already cannot reach the target, so no plan exists |
| `not_found` | the search came up empty and nothing was proven, which is not the same thing |

`macrosolver/bounds.py` never runs the search. It relaxes the problem until the
optimum is a greedy pick, which makes the bound exact for the relaxation and
therefore valid for the real problem:

* One meal holds at most `max_foods_per_meal` distinct foods, so the plan has at most
  `days x meals_per_day x max_foods_per_meal` food-in-meal appearances, and food *f*
  may use at most `cap_f` of them.
* Each appearance of *f* contributes at most `max_servings x amount_f` of a macro.
  Maximising subject to those two limits is a fractional knapsack with unit weights,
  so sorting by amount and taking greedily is exactly optimal.
* Every meal slot holds at least one food and at least one serving, and all macro
  amounts are non-negative, so the minimum sits at exactly `days x meals_per_day`
  appearances taking the smallest amounts first.

`tests/test_exhaustive.py` checks those bounds against brute force too: the claimed
maximum must be at or above the real maximum over every legal plan, and the claimed
minimum at or below the real minimum.

## The diagnosis

"No solution" is not an answer, so an infeasible instance names the constraint and
quantifies the gap. Real output from `fixtures/infeasible_protein.json`:

```
status: proven_infeasible

Why no plan can exist (proven, not guessed):
  [macro:protein_g:below_target]
    protein (g) target needs at least 175.0 per day (1,225.0 across 7 days), but the
    pantry's maximum under the frequency caps and the 3 foods per meal limit is 95.0
    per day (665.0 across 7 days), short by 80.0 per day
```

The certificate carries the same numbers in machine-readable form, including which
foods formed the binding pick. When the search fails without a certificate you get
the achievable window for every macro and the closest plan it managed, labelled as a
search failure rather than a proof.

## The independent checker

`checker/independent_check.py` imports nothing from `macrosolver`. It reads the
result document and `data/foods.json` off disk and recomputes every daily total,
every accepted window, every food's meal-slot count, every meal repeat count and the
distinct-food count from the raw per-serving numbers. It also compares the numbers
the solver *reported* against its own recount, so a solver that returns a valid plan
but lies about its macros is caught as well as one that returns an invalid plan.

A checker that shares code with the thing it checks inherits its bugs, so
`tests/test_checker_can_fail.py` takes a genuinely valid document and breaks exactly
one thing in it, nineteen times over, asserting each break is caught. The browser
check feeds the page's own solution to that same checker.

## The starter food table

Deliberately small. Sixteen foods, every one traceable. Ten well-sourced foods beat a
hundred guessed ones, so nothing was added that could not be cited, and no number in
this repository was typed in by hand.

Nutrient values come from the USDA FoodData Central SR Legacy CSV distribution
(`FoodData_Central_sr_legacy_food_csv_2018-04.zip`, sha256
`b80817294b8850530aaedf2e515c02593b1824f763a0ff356e5c2081643e6fd0`, retrieved
2026-07-31, public domain). `scripts/build_food_table.py` extracts the rows for these
foods into `data/usda_source_rows.csv` and `data/usda_portion_rows.csv`, which are
committed, and derives `data/foods.json` from them by scaling per-100-g figures to
the serving size. Every serving size is itself a USDA `food_portion` row for the same
food, so the gram weights are cited rather than chosen.

`scripts/build_food_table.py --check` rebuilds the table and fails if it differs, and
`tests/test_food_table.py` recomputes every figure from the CSV a second time with
arithmetic written separately, so a bug in the build script cannot hide behind its
own rebuild.

The links below are FoodData Central's record endpoint, which returns the food's data
and answers HTTP 200. The human-readable portal page is
`https://fdc.nal.usda.gov/food-details/<fdc_id>/nutrients`; it renders the right food
but its server answers 404, because the portal is a single-page app whose server does
not know that route. Both URLs are in `data/foods.json` and
`scripts/check_links.py` resolves them all.

| id | USDA description | serving | kcal | protein g | carb g | fat g | fibre g | sodium mg | source |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `chicken_breast` | Chicken, broilers or fryers, breast, meat only, cooked, roasted | 0.5 breast, bone and skin removed (86 g) | 142 | 26.7 | 0.0 | 3.1 | 0.0 | 64 | [FDC 171477](https://fdc.nal.usda.gov/portal-data/external/171477) |
| `egg` | Egg, whole, cooked, hard-boiled | 1 large (50 g) | 78 | 6.3 | 0.6 | 5.3 | 0.0 | 62 | [FDC 173424](https://fdc.nal.usda.gov/portal-data/external/173424) |
| `white_rice` | Rice, white, long-grain, regular, enriched, cooked | 1 cup (158 g) | 205 | 4.3 | 44.5 | 0.4 | 0.6 | 2 | [FDC 168878](https://fdc.nal.usda.gov/portal-data/external/168878) |
| `lentils` | Lentils, mature seeds, cooked, boiled, without salt | 1 cup (198 g) | 230 | 17.9 | 39.9 | 0.8 | 15.6 | 4 | [FDC 172421](https://fdc.nal.usda.gov/portal-data/external/172421) |
| `oats` | Cereals, oats, regular and quick, not fortified, dry | 1 cup (81 g) | 307 | 10.7 | 54.8 | 5.3 | 8.2 | 5 | [FDC 173904](https://fdc.nal.usda.gov/portal-data/external/173904) |
| `broccoli` | Broccoli, cooked, boiled, drained, without salt | 0.5 cup, chopped (78 g) | 27 | 1.9 | 5.6 | 0.3 | 2.6 | 32 | [FDC 169967](https://fdc.nal.usda.gov/portal-data/external/169967) |
| `sweet_potato` | Sweet potato, cooked, baked in skin, flesh, without salt | 1 cup (200 g) | 180 | 4.0 | 41.4 | 0.3 | 6.6 | 72 | [FDC 168483](https://fdc.nal.usda.gov/portal-data/external/168483) |
| `olive_oil` | Oil, olive, salad or cooking | 1 tablespoon (13.5 g) | 119 | 0.0 | 0.0 | 13.5 | 0.0 | 0 | [FDC 171413](https://fdc.nal.usda.gov/portal-data/external/171413) |
| `almonds` | Nuts, almonds | 1 oz (23 whole kernels) (28.35 g) | 164 | 6.0 | 6.1 | 14.2 | 3.5 | 0 | [FDC 170567](https://fdc.nal.usda.gov/portal-data/external/170567) |
| `greek_yogurt` | Yogurt, Greek, plain, nonfat (Includes foods for USDA's Food Distribution Program) | 1 container (170 g) | 100 | 17.3 | 6.1 | 0.7 | 0.0 | 61 | [FDC 170894](https://fdc.nal.usda.gov/portal-data/external/170894) |
| `salmon` | Fish, salmon, Atlantic, farmed, cooked, dry heat | 3 oz (85 g) | 175 | 18.8 | 0.0 | 10.5 | 0.0 | 52 | [FDC 175168](https://fdc.nal.usda.gov/portal-data/external/175168) |
| `black_beans` | Beans, black, mature seeds, cooked, boiled, without salt | 1 cup (172 g) | 227 | 15.2 | 40.8 | 0.9 | 15.0 | 2 | [FDC 173735](https://fdc.nal.usda.gov/portal-data/external/173735) |
| `banana` | Bananas, raw | 1 medium (7" to 7-7/8" long) (118 g) | 105 | 1.3 | 27.0 | 0.4 | 3.1 | 1 | [FDC 173944](https://fdc.nal.usda.gov/portal-data/external/173944) |
| `tofu` | Tofu, raw, firm, prepared with calcium sulfate | 0.5 cup (126 g) | 181 | 21.8 | 3.5 | 11.0 | 2.9 | 18 | [FDC 172475](https://fdc.nal.usda.gov/portal-data/external/172475) |
| `spinach` | Spinach, raw | 1 cup (30 g) | 7 | 0.9 | 1.1 | 0.1 | 0.7 | 24 | [FDC 168462](https://fdc.nal.usda.gov/portal-data/external/168462) |
| `cottage_cheese` | Cheese, cottage, lowfat, 1% milkfat | 4 oz (113 g) | 81 | 14.0 | 3.1 | 1.2 | 0.0 | 459 | [FDC 173417](https://fdc.nal.usda.gov/portal-data/external/173417) |

## The browser page

`docs/index.html` is self-contained: inline CSS, inline JS, the food table inlined
from `data/foods.json` by `scripts/sync_page_data.py`, and no remote asset of any
kind. The only outbound links are the FoodData Central citations. It carries the same
framing as this README, above the fold and inside a bordered block.

It ports the solver, the bounds and the certificates to JavaScript, and produces a
result document in exactly the shape `macrosolver/report.py` produces.
`browser/check_page.js` drives Chromium, hands the page the same instance the Python
side solves, and runs `checker/independent_check.py` over whatever comes back. The
page's own bookkeeping is never trusted.

Met and missed are never conveyed by colour alone: every status badge carries a word
(`MET` / `MISSED`) and a shape (filled circle / triangle) as well as a colour, and the
browser check asserts that on every badge. Light and dark both come from
`prefers-color-scheme`, and `:root[data-theme="light"]` / `:root[data-theme="dark"]`
override it in both directions. There is no horizontal body scroll at a 390 px
viewport; wide tables scroll inside their own containers, which the check confirms by
walking every element rather than by hiding overflow.

## Attacking the verify

A verify that passes on a broken implementation is worth nothing, so
`scripts/attack.sh` copies the tree, breaks the solver on purpose, and confirms the
verify notices. Each sabotage is proven observable before the verify runs, because an
attack that quietly did nothing produces a passing verify and the wrong conclusion.

```
$ bash scripts/attack.sh
======================================================================
ATTACK A: the solver stops enforcing frequency caps
======================================================================
patched macrosolver/solver.py: 3 cap checks disabled

proving the sabotage is real before trusting any conclusion from it:
  solver exit 0, status solved
  caps actually broken by the sabotaged solver: {'almonds': (3, 1), 'greek_yogurt': (2, 1), 'oats': (4, 1), 'olive_oil': (3, 1), 'tofu': (4, 1), 'white_rice': (4, 1)}
  fixtures/feasible_week.json now carries those caps

--- running the full verify inside the sabotaged copy ---
verify exit code: 1
the checks that fired:
    FAIL  unit tests (exit 1)
    FAIL  the returned plan does not satisfy its own constraints
the independent recount said:
  INDEPENDENT CHECK FAILED: 6 problem(s) out of 481 checks
RESULT: ATTACK A (ignore frequency caps) made verify fail with exit 1, as it must.

======================================================================
ATTACK B: the solver returns the same meal every day
======================================================================
patched macrosolver/solver.py: every slot gets the first meal

proving the sabotage is real before trusting any conclusion from it:
  solver exit 0, status solved
  distinct meals in the returned week: 1
  most repeated meal: ('2x broccoli + 2x tofu + white_rice', 21)
  distinct foods used: 3 (instance asks for 8)

--- running the full verify inside the sabotaged copy ---
verify exit code: 1
the checks that fired:
    FAIL  unit tests (exit 1)
    FAIL  the returned plan does not satisfy its own constraints
the independent recount said:
  INDEPENDENT CHECK FAILED: 16 problem(s) out of 453 checks
RESULT: ATTACK B (same meal every day) made verify fail with exit 1, as it must.

Both attacks were caught. The verify is not vacuous.
```

Both attacks were caught by the independent recount, not by the solver's own
bookkeeping: attack A disables `_allowed`, `_fits_empty_slot` **and** `hard_ok`, which
is every self-check the solver has, and the plan is still rejected because the checker
counts the meal slots itself. Exit codes: attack A verify `1`, attack B verify `1`,
attack script overall `0`.

## Limitations

* **Not proven optimal.** The search returns a good plan, not a provably closest one.
  Optimality is measured against brute force only on instances small enough to
  enumerate, and those results do not transfer to a full week.
* **`not_found` is a search failure, not a proof.** It may mean no plan exists, or it
  may mean this search did not find one. The tool says which of the two it knows.
* **Calorie targets and macro targets can contradict each other.** Roughly, energy is
  4 kcal per gram of protein and carbohydrate and 9 per gram of fat, so a request for
  2,200 kcal alongside 150 g protein, 220 g carbohydrate and 70 g fat is asking for
  about 90 kcal that the macros cannot supply. The tool does not detect this and will
  report `not_found` rather than explaining it. Widening the calorie tolerance or
  making the numbers consistent is the fix.
* **Reference data, not your food.** USDA SR Legacy entries are averages of sampled
  items. Brand, cut, cooking method and moisture loss all move the numbers.
* **Whole servings only.** A serving is an integer unit. Fractional servings, weight
  targets and recipes with instructions are all out of scope.
* **Meals are unordered sets of foods.** There is no notion of a breakfast, no cooking
  time, no shopping list, and no check that a combination is edible. A meal of olive
  oil and spinach is a legal meal.
* **The starter pantry is 16 foods.** That is a deliberate choice about provenance, not
  a limit of the solver. Add your own to `data/servings.json` and rerun
  `scripts/build_food_table.py --usda-dir <unpacked SR Legacy CSVs>`.
* **No micronutrients beyond fibre and sodium.** The extraction pulls six nutrients.
  Adding more is a change to one dictionary in `scripts/build_food_table.py` plus a
  re-extraction, but the tests and the checker would need the new keys too.
* **Candidate meals are capped at 8,000.** A very large pantry gets a deterministic
  sample rather than the full enumeration, which makes the search weaker on those
  instances. The bounds and the certificates are unaffected.

## Layout

```
macrosolver/          the solver: model, food loading, bounds, search, report, CLI
checker/              the independent constraint checker, imports none of the above
data/                 USDA source rows, portion rows, serving choices, foods.json
fixtures/             known-feasible, known-infeasible, trivial and malformed instances
tests/                71 tests, run with: python3 -m unittest discover -s tests -t tests
docs/index.html       self-contained page with a JavaScript port of the solver
browser/check_page.js real-browser check, drives Chromium and calls the checker
scripts/              build, sync, verify, attack, link check, tracked-file scan
```

## Status

Real output, pasted from a run on 2026-07-31. `scripts/verify.sh` exits 0 only
when every check below passes, and one of those checks is that this section still
carries the success line and the current test count.

```
$ bash scripts/verify.sh
1. food data provenance
data/foods.json matches a rebuild from the USDA rows (16 foods)
  ok    data/foods.json is a rebuild of the committed USDA rows
docs/index.html food table is in sync with data/foods.json (16 foods)
  ok    docs/index.html carries the same food table

2. unit tests, including the controls that must make the checker fail
Ran 71 tests in 9.887s

OK
  ok    unit tests (71 tests)
  ok    the suite is not a stub (71 tests)

3. end to end exit codes
  ok    a known-feasible week solves (exit 0)
  ok    a trivially feasible instance is not reported infeasible (exit 0)
  ok    a known-infeasible instance is reported infeasible (exit 1)
  ok    a malformed instance is rejected, not solved (exit 3)
  ok    a missing instance file is rejected (exit 3)

4. the returned plan, recounted by the independent checker
INDEPENDENT CHECK PASSED: 483 checks, status 'solved'
  ok    the week's plan satisfies every constraint on a recount
  ok    the trivial instance's plan satisfies every constraint on a recount

   achieved against target, per day, read back from the written document:
   status: solved
   
    day          energy (kcal)            protein (g)       carbohydrate (g)                fat (g)
        target  achieved  error target  achieved  error target  achieved  error target  achieved  error
      1  2150.0   2056.8   -93.2   150.0    152.3    +2.3   220.0    217.7    -2.3    70.0     70.5    +0.5
      2  2150.0   2074.3   -75.7   150.0    147.0    -3.0   220.0    221.7    +1.7    70.0     70.1    +0.1
      3  2150.0   2046.5  -103.5   150.0    149.9    -0.1   220.0    222.6    +2.6    70.0     71.3    +1.3
      4  2150.0   2043.4  -106.6   150.0    151.2    +1.2   220.0    224.7    +4.7    70.0     69.8    -0.2
      5  2150.0   2089.5   -60.5   150.0    150.3    +0.3   220.0    223.0    +3.0    70.0     68.5    -1.5
      6  2150.0   2058.9   -91.1   150.0    150.6    +0.6   220.0    224.8    +4.8    70.0     70.8    +0.8
      7  2150.0   2055.7   -94.3   150.0    151.1    +1.1   220.0    235.6   +15.6    70.0     72.5    +2.5
   
     energy (kcal)        target   2150.0  allowance   107.5  mean error   -89.28  worst error  -106.58  7/7 days [ok] MET
     protein (g)          target    150.0  allowance    12.0  mean error    +0.35  worst error    -2.95  7/7 days [ok] MET
     carbohydrate (g)     target    220.0  allowance    30.0  mean error    +4.30  worst error   +15.64  7/7 days [ok] MET
     fat (g)              target     70.0  allowance    12.0  mean error    +0.51  worst error    +2.54  7/7 days [ok] MET
   
     distinct foods used 15 (minimum 8)
     most repeated meal used 2 time(s) (limit 3)
     chicken_breast     used  2 of  5 allowed meal slots
     egg                used  4 of  6 allowed meal slots
     olive_oil          used  5 of  7 allowed meal slots
     salmon             used  3 of  3 allowed meal slots

5. the infeasibility diagnosis
  ok    the infeasible instance exits 1
  ok    the infeasibility report is well formed and carries a certificate
   protein (g) target needs at least 175.0 per day (1,225.0 across 7 days), but the pantry's maximum under the frequency caps and the 3 foods per meal limit is 95.0 per day (665.0 across 7 days), short by 80.0 per day
  ok    the diagnosis names the constraint and the size of the gap

6. determinism
  ok    same seed, fresh process, same plan (fa76006744f82725)

7. the page, in a real browser
   64/64 browser checks passed
  ok    the page runs, solves, and its plan passes the same independent checker

8. secrets and absolute paths in tracked files
scanned 37 tracked files as bytes: no credential-shaped strings, no absolute home paths
  ok    no credential-shaped strings and no absolute home paths

9. the README
  ok    README.md exists
  ok    README has a Status section
  ok    the Status section carries this script's success line
  ok    the test count in the README still matches a real run (71)
  ok    README states: not nutrition advice
  ok    README states: USDA FoodData Central
  ok    README states: not proven

macro-solver verify: ALL CHECKS PASSED
repo: ~/Projects/thousand/projects/macro-solver
```
