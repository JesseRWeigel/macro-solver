#!/usr/bin/env bash
# Verify macro-solver. Exit 0 only when the tool genuinely works.
#
#  1. The food table traces back to USDA rows, and the page's copy is not stale.
#  2. Unit tests, including the controls that prove the independent checker fails.
#  3. End to end exit codes for solved, proven infeasible, and malformed input.
#  4. Every returned plan is recounted from data/foods.json by code that shares
#     nothing with the solver, and the achieved macros are printed with the error.
#  5. The known-infeasible instance names its binding constraint and the gap.
#  6. The same seed produces the same plan in a fresh process.
#  7. The page is loaded in a real browser and its plan goes through the same
#     independent checker. A missing browser is a failure, never a skip.
#  8. No secrets and no absolute home paths in tracked files.
#  9. The README carries a Status section with this script's own success line, and
#     the test count it quotes still matches a real run.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SUCCESS_LINE="macro-solver verify: ALL CHECKS PASSED"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
pass() { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }

expect_exit() {
  local want="$1" label="$2"; shift 2
  set +e
  "$@" >/dev/null 2>&1
  local got=$?
  set -e
  if [ "$got" = "$want" ]; then pass "$label (exit $got)"
  else bad "$label (want exit $want, got $got)"; fi
}

echo "1. food data provenance"
if python3 scripts/build_food_table.py --check; then
  pass "data/foods.json is a rebuild of the committed USDA rows"
else
  bad "data/foods.json does not match the USDA rows"
fi
if python3 scripts/sync_page_data.py --check; then
  pass "docs/index.html carries the same food table"
else
  bad "docs/index.html food table is stale"
fi

echo
echo "2. unit tests, including the controls that must make the checker fail"
set +e
python3 -m unittest discover -s tests -t tests -q >"$WORK/tests.txt" 2>&1
tests_rc=$?
set -e
tail -3 "$WORK/tests.txt"
TEST_COUNT="$(grep -oE '^Ran [0-9]+ test' "$WORK/tests.txt" | grep -oE '[0-9]+' || true)"
if [ "$tests_rc" = 0 ]; then pass "unit tests ($TEST_COUNT tests)"
else bad "unit tests (exit $tests_rc)"; sed -n '1,60p' "$WORK/tests.txt"; fi
if [ -n "$TEST_COUNT" ] && [ "$TEST_COUNT" -ge 60 ]; then
  pass "the suite is not a stub ($TEST_COUNT tests)"
else
  bad "expected at least 60 tests, saw '${TEST_COUNT:-none}'"
fi

echo
echo "3. end to end exit codes"
expect_exit 0 "a known-feasible week solves" \
  python3 -m macrosolver fixtures/feasible_week.json --quiet
expect_exit 0 "a trivially feasible instance is not reported infeasible" \
  python3 -m macrosolver fixtures/trivially_feasible.json --quiet
expect_exit 1 "a known-infeasible instance is reported infeasible" \
  python3 -m macrosolver fixtures/infeasible_protein.json --quiet
expect_exit 3 "a malformed instance is rejected, not solved" \
  python3 -m macrosolver fixtures/malformed.json --quiet
expect_exit 3 "a missing instance file is rejected" \
  python3 -m macrosolver fixtures/does-not-exist.json --quiet

echo
echo "4. the returned plan, recounted by the independent checker"
python3 -m macrosolver fixtures/feasible_week.json --quiet --out "$WORK/feasible.json"
if python3 checker/independent_check.py "$WORK/feasible.json" --expect solved; then
  pass "the week's plan satisfies every constraint on a recount"
else
  bad "the returned plan does not satisfy its own constraints"
fi
python3 -m macrosolver fixtures/trivially_feasible.json --quiet --out "$WORK/trivial.json"
if python3 checker/independent_check.py "$WORK/trivial.json" --expect solved --quiet; then
  pass "the trivial instance's plan satisfies every constraint on a recount"
else
  bad "the trivial instance's plan does not satisfy its own constraints"
fi

echo
echo "   achieved against target, per day, read back from the written document:"
python3 scripts/summarise.py "$WORK/feasible.json" | sed 's/^/   /'

echo
echo "5. the infeasibility diagnosis"
set +e
python3 -m macrosolver fixtures/infeasible_protein.json --quiet --out "$WORK/infeasible.json"
inf_rc=$?
set -e
if [ "$inf_rc" = 1 ]; then pass "the infeasible instance exits 1"
else bad "the infeasible instance exited $inf_rc"; fi
if python3 checker/independent_check.py "$WORK/infeasible.json" \
     --expect proven_infeasible --quiet; then
  pass "the infeasibility report is well formed and carries a certificate"
else
  bad "the infeasibility report does not carry a usable certificate"
fi
DIAG="$(python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
c=[x for x in d['certificates'] if x['constraint']=='macro:protein_g:below_target']
print(c[0]['message'] if c else 'NO PROTEIN CERTIFICATE')
" "$WORK/infeasible.json")"
echo "   $DIAG"
case "$DIAG" in
  *"short by"*"per day"*) pass "the diagnosis names the constraint and the size of the gap" ;;
  *) bad "the diagnosis does not quantify the gap" ;;
esac

echo
echo "6. determinism"
A="$(python3 -m macrosolver fixtures/feasible_week.json --quiet --out "$WORK/a.json" \
     && python3 -c "import json,hashlib,sys;print(hashlib.sha256(json.dumps(json.load(open('$WORK/a.json'))['plan'],sort_keys=True).encode()).hexdigest())")"
B="$(PYTHONHASHSEED=98765 python3 -m macrosolver fixtures/feasible_week.json --quiet --out "$WORK/b.json" \
     && python3 -c "import json,hashlib,sys;print(hashlib.sha256(json.dumps(json.load(open('$WORK/b.json'))['plan'],sort_keys=True).encode()).hexdigest())")"
if [ "$A" = "$B" ]; then pass "same seed, fresh process, same plan (${A:0:16})"
else bad "the plan changed between processes: $A vs $B"; fi

echo
echo "7. the page, in a real browser"
# Overridable so a copied tree (see scripts/attack.sh) can still reach the
# browser. Unset, it resolves next to this project as it does in the fleet.
# Ordinary places first, with the sibling project last. Putting that sibling FIRST is the shape
# that made six projects in this catalog pass verify in place and fail in every fresh clone.
# PLAYWRIGHT_CORE is honoured as well as the project-specific name, because every other project
# here uses that one and a reader should not have to guess which.
PW=""
for cand in "${MACRO_SOLVER_PLAYWRIGHT:-}" "${PLAYWRIGHT_CORE:-}" \
            "$PWD/node_modules/playwright-core" \
            "$HOME/<repo>/a11y-sweep/node_modules/playwright-core" \
            "../a11y-sweep/node_modules/playwright-core"; do
  [ -n "$cand" ] && [ -d "$cand" ] && { PW="$cand"; break; }
done
[ -z "$PW" ] && PW="${MACRO_SOLVER_PLAYWRIGHT:-${PLAYWRIGHT_CORE:-../a11y-sweep/node_modules/playwright-core}}"
if [ ! -d "$PW" ]; then
  # A skipped browser check is "could not verify", never "verified".
  bad "playwright-core is not available, so the page was never loaded.
        This is a failure rather than a skip: a check that did not run reports the same
        success as one that ran and passed.
        To run it:  npm install --no-save playwright-core && npx playwright install chromium
        Or:         PLAYWRIGHT_CORE=/path/to/playwright-core
        The 24 solver and independent-checker checks above all run without a browser."
else
  set +e
  node browser/check_page.js >"$WORK/browser.txt" 2>&1
  br_rc=$?
  set -e
  tail -1 "$WORK/browser.txt" | sed 's/^/   /'
  if [ "$br_rc" = 0 ]; then
    pass "the page runs, solves, and its plan passes the same independent checker"
  else
    bad "the browser check failed (exit $br_rc)"
    sed -n '1,80p' "$WORK/browser.txt"
  fi
fi

echo
echo "8. secrets and absolute paths in tracked files"
if python3 scripts/scan_tracked.py; then
  pass "no credential-shaped strings and no absolute home paths"
else
  bad "the tracked-file scan found something"
fi

echo
echo "9. the README"
if [ -f README.md ]; then pass "README.md exists"; else bad "README.md is missing"; fi
if grep -q '^## Status' README.md; then pass "README has a Status section"
else bad "README has no Status section"; fi
if grep -qF "$SUCCESS_LINE" README.md; then
  pass "the Status section carries this script's success line"
else
  bad "the Status section does not carry '$SUCCESS_LINE'"
fi
if [ -n "$TEST_COUNT" ] && grep -qF "Ran $TEST_COUNT tests" README.md; then
  pass "the test count in the README still matches a real run ($TEST_COUNT)"
else
  bad "the README does not quote 'Ran $TEST_COUNT tests', so its pasted output is stale"
fi
for phrase in "not nutrition advice" "USDA FoodData Central" "not proven"; do
  if grep -qi "$phrase" README.md; then pass "README states: $phrase"
  else bad "README does not mention: $phrase"; fi
done

echo
if [ "$fail" = 0 ]; then
  echo "$SUCCESS_LINE"
  echo "repo: ${PWD/#$HOME/\~}"
  exit 0
fi
echo "macro-solver verify: FAILED"
exit 1
