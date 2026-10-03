#!/bin/bash
# Everything, in one command. Run before and after any change to a core mechanism.
cd "$(dirname "$0")"
PY=.venv/bin/python
if [ -f .mutants.lock ]; then
  echo "A mutation run is rewriting game/sim.py (.mutants.lock). Wait for it to finish."
  exit 2
fi
out=$(mktemp)
fail=0
start=$(date +%s)

run() {
  echo ""
  echo "═══ $1 ═══"
  shift 1
  $PY -m "game.$1" "${@:2}" >"$out" 2>&1
  local rc=$?
  grep -v "^pygame\|^Hello from" "$out"
  # Count what each harness asserted, so the docs check can verify the BUILD-LOG's
  # summary table without paying to run these a second time.
  local n
  n=$(grep -cE "^  (PASS|FAIL)" "$out")
  case "$1" in
    checks) export PAPER_SOVEREIGN_COUNT_checks="$n" ;;
    inputs) export PAPER_SOVEREIGN_COUNT_inputs="$n" ;;
  esac
  if [ $rc -ne 0 ]; then echo "  ^^ game.$1 exited $rc"; fail=1; fi
}

echo "PAPER SOVEREIGN — full verification"
run "CORRECTNESS  (invariants, bounds, mechanism)" checks
run "CONTENT  (is any of it reachable?)" deadcontent
run "ROBUSTNESS  (degenerate and adversarial play)" adversarial
run "INPUT  (do the controls do what they say?)" inputs
# The renderer, headless: every panel drawn, every string checked for landing
# inside the window. Four layout bugs got to a committed build by eye alone,
# including the end screen's closing line being drawn below the bottom.
shots_dir=$(mktemp -d)
run "INTERFACE  (does it fit on the screen?)" shots "$shots_dir"
rm -rf "$shots_dir"
run "DOCS  (does the README still describe this game?)" readme_check
run "SCALING  (how far does this go?)" bench
rm -f "$out"

echo ""
echo "═══════════════════════════════════════"
if [ $fail -eq 0 ]; then
  echo "ALL GREEN   ($(( $(date +%s) - start ))s)"
else
  echo "FAILURES ABOVE   ($(( $(date +%s) - start ))s)"
fi
exit $fail
