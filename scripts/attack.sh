#!/usr/bin/env bash
# Attack the verify. Copy the tree, break the solver on purpose, and confirm that
# scripts/verify.sh notices.
#
# Two sabotages, both aimed at the constraints the solver enforces for itself:
#   A. the solver stops enforcing frequency caps
#   B. the solver returns the same meal in every slot of the week
#
# Each attack is proven observable BEFORE the verify is run, because an attack
# that quietly did nothing produces a passing verify and the wrong conclusion.
# The step that has to catch each one is the independent recount, so this script
# reports that check's verdict separately from the whole verify's exit code.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
SRC="$PWD"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

overall=0

prepare() {
  local dst="$1"
  rm -rf "$dst"
  mkdir -p "$dst"
  git -C "$SRC" ls-files -z | while IFS= read -r -d '' f; do
    mkdir -p "$dst/$(dirname "$f")"
    cp "$SRC/$f" "$dst/$f"
  done
  # A git repo is needed because the tracked-file scan walks `git ls-files`.
  git -C "$dst" init -q
  git -C "$dst" add -A
  git -C "$dst" -c user.email=attack@example.invalid -c user.name=attack \
      commit -q -m sabotage
}

# Resolved once, so the sabotaged copy sitting in a temp directory can still run
# the browser step. Without this the copy would fail on a missing browser and the
# attack would prove less than it looks like it proves.
if [ -d "$SRC/../a11y-sweep/node_modules/playwright-core" ]; then
  export MACRO_SOLVER_PLAYWRIGHT="$(cd "$SRC/../a11y-sweep/node_modules/playwright-core" && pwd)"
fi

report() {
  local label="$1" dir="$2"
  echo
  echo "--- running the full verify inside the sabotaged copy ---"
  set +e
  bash "$dir/scripts/verify.sh" >"$WORK/out.txt" 2>&1
  local rc=$?
  set -e
  echo "verify exit code: $rc"
  echo "the checks that fired:"
  grep -E '^  FAIL' "$WORK/out.txt" | head -12 | sed 's/^/  /' || true
  echo "the independent recount said:"
  grep -E 'INDEPENDENT CHECK' "$WORK/out.txt" | head -4 | sed 's/^/  /' || \
    echo "  (the recount step was never reached)"
  if [ "$rc" -eq 0 ]; then
    echo "RESULT: $label did NOT make verify fail. The verify has a gap."
    overall=1
  else
    echo "RESULT: $label made verify fail with exit $rc, as it must."
  fi
}

########################################################################
echo "======================================================================"
echo "ATTACK A: the solver stops enforcing frequency caps"
echo "======================================================================"
A="$WORK/attack-caps"
prepare "$A"

python3 - "$A" <<'PY'
import sys, re
path = sys.argv[1] + "/macrosolver/solver.py"
src = open(path).read()
before = src
# Every gate the solver uses to keep itself inside the frequency caps.
src = src.replace(
    "    def _allowed(self, cand_idx, slot, plan, usage, repeats):",
    "    def _allowed(self, cand_idx, slot, plan, usage, repeats):\n        return True  # SABOTAGE",
)
src = src.replace(
    '        """Whether meal `ci` can be added to a slot that holds nothing yet."""',
    '        """Whether meal `ci` can be added to a slot that holds nothing yet."""\n        return True  # SABOTAGE',
)
src = src.replace(
    "    def hard_ok(self, plan) -> bool:",
    "    def hard_ok(self, plan) -> bool:\n        return True  # SABOTAGE",
)
assert src.count("SABOTAGE") == 3, f"patch did not apply, {src.count('SABOTAGE')} of 3"
assert src != before
open(path, "w").write(src)
print("patched macrosolver/solver.py: 3 cap checks disabled")
PY

echo
echo "proving the sabotage is real before trusting any conclusion from it:"
python3 - "$A" <<'PY'
import json, subprocess, sys, os
root = sys.argv[1]
spec = json.load(open(os.path.join(root, "fixtures", "feasible_week.json")))
spec["frequency_caps"] = {f: 1 for f in
                          ["oats", "greek_yogurt", "olive_oil", "almonds",
                           "white_rice", "tofu"]}
path = os.path.join(root, "fixtures", "attack_caps.json")
json.dump(spec, open(path, "w"), indent=2)

out = os.path.join(root, "attack-result.json")
rc = subprocess.run([sys.executable, "-m", "macrosolver", path, "--quiet",
                     "--out", out], cwd=root).returncode
doc = json.load(open(out))
caps = doc["instance"]["frequency_caps"]
broken = {f: (n, caps[f]) for f, n in doc["food_usage"].items()
          if f in caps and n > caps[f]}
print(f"  solver exit {rc}, status {doc['status']}")
print(f"  caps actually broken by the sabotaged solver: {broken}")
if not broken:
    print("  the sabotage was a no-op; stop here rather than drawing a conclusion")
    sys.exit(9)
PY

# Point the verify's own fixture at those caps so the sabotage shows up there too.
python3 - "$A" <<'PY'
import json, os, sys
root = sys.argv[1]
path = os.path.join(root, "fixtures", "feasible_week.json")
spec = json.load(open(path))
spec["frequency_caps"] = {f: 1 for f in
                          ["oats", "greek_yogurt", "olive_oil", "almonds",
                           "white_rice", "tofu"]}
json.dump(spec, open(path, "w"), indent=2)
print("  fixtures/feasible_week.json now carries those caps")
PY

report "ATTACK A (ignore frequency caps)" "$A"

########################################################################
echo
echo "======================================================================"
echo "ATTACK B: the solver returns the same meal every day"
echo "======================================================================"
B="$WORK/attack-repeat"
prepare "$B"

python3 - "$B" <<'PY'
import sys
path = sys.argv[1] + "/macrosolver/solver.py"
src = open(path).read()
before = src
needle = """        if search.feasible(plan):
            return SolveResult(
                status="solved",
                plan=[cands[i] for i in plan],"""
replacement = """        if search.feasible(plan):
            plan = [plan[0]] * len(plan)  # SABOTAGE: one meal, every slot
            return SolveResult(
                status="solved",
                plan=[cands[i] for i in plan],"""
assert needle in src, "patch target not found"
src = src.replace(needle, replacement)
assert src != before
open(path, "w").write(src)
print("patched macrosolver/solver.py: every slot gets the first meal")
PY

echo
echo "proving the sabotage is real before trusting any conclusion from it:"
python3 - "$B" <<'PY'
import json, os, subprocess, sys
root = sys.argv[1]
out = os.path.join(root, "attack-result.json")
rc = subprocess.run([sys.executable, "-m", "macrosolver",
                     os.path.join(root, "fixtures", "feasible_week.json"),
                     "--quiet", "--out", out], cwd=root).returncode
doc = json.load(open(out))
meals = doc["meal_repeats"]
print(f"  solver exit {rc}, status {doc['status']}")
print(f"  distinct meals in the returned week: {len(meals)}")
print(f"  most repeated meal: {max(meals.items(), key=lambda kv: kv[1])}")
print(f"  distinct foods used: {doc['distinct_foods']} "
      f"(instance asks for {doc['instance']['min_distinct_foods']})")
if len(meals) != 1:
    print("  the sabotage was a no-op; stop here rather than drawing a conclusion")
    sys.exit(9)
PY

report "ATTACK B (same meal every day)" "$B"

echo
if [ "$overall" = 0 ]; then
  echo "Both attacks were caught. The verify is not vacuous."
  exit 0
fi
echo "At least one attack slipped through. Fix the verify."
exit 1
