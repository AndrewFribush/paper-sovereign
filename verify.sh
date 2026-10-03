#!/bin/bash
set -eu

case "${1:-}" in
  ""|--mutations) ;;
  *) echo "Usage: ./verify.sh [--mutations]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: ./verify.sh [--mutations]" >&2
  exit 2
fi

project_root="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$project_root/.venv/bin/python" ]; then
  echo "Create .venv and install requirements.txt first; see README.md." >&2
  exit 2
fi
if [ -f "$project_root/.mutants.lock" ]; then
  echo "A mutation run is changing the source. Wait for it to finish." >&2
  exit 2
fi

run_dir="$(mktemp -d "${TMPDIR:-/tmp}/paper-sovereign-checks.XXXXXX")"
trap 'rm -rf "$run_dir"' EXIT
mkdir "$run_dir/game" "$run_dir/docs" "$run_dir/tmp"
export TMPDIR="$run_dir/tmp"
cp "$project_root"/game/*.py "$run_dir/game/"
cp "$project_root"/docs/*.md "$run_dir/docs/"
cp "$project_root/README.md" "$run_dir/README.md"
cp "$project_root/scripts/run-checks.sh" "$run_dir/run-checks.sh"
ln -s "$project_root/.venv" "$run_dir/.venv"
cd "$run_dir"

if [ "${1:-}" = --mutations ]; then
  .venv/bin/python -m game.mutants
else
  bash ./run-checks.sh
fi
