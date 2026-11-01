#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command="${1:?Usage: scripts/ci.sh source|build|format|contract|test SUITE}"
case "$command" in
  source)
    git diff --check
    for script in scripts/*.sh; do bash -n "$script"; done
    python3 -m py_compile scripts/*.py
    ;;
  build) uv build ;;
  format)
    uv run ruff check .
    uv run ruff format --check .
    ;;
  contract)
    uv run knowledge export-contract
    git diff --exit-code HEAD -- contracts/
    test -z "$(git ls-files --others --exclude-standard -- contracts/)"
    ;;
  test)
    suite="${2:?Expected domain, application, infrastructure or api}"
    case "$suite" in domain|application|infrastructure|api) ;; *) echo "Unknown suite: $suite" >&2; exit 2 ;; esac
    report="artifacts/verification/$suite/results.xml"
    mkdir -p "$(dirname "$report")"
    status=0
    uv run pytest -q "tests/$suite" --junitxml="$report" || status=$?
    python3 scripts/summarize-tests.py "$report" "$suite tests" \
      "${VERIFY_SUMMARY_FILE:-artifacts/verification/summary.md}" || { if (( status == 0 )); then exit 1; fi; }
    exit "$status"
    ;;
  *) echo "Unknown verification command: $command" >&2; exit 2 ;;
esac
