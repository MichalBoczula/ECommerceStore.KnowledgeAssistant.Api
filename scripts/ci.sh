#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command="${1:?Usage: scripts/ci.sh source|build|format|contract|evaluation|test SUITE}"
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
  evaluation)
    report_dir="artifacts/verification/evaluation"
    status=0
    uv run knowledge evaluate --suite evaluation/demo-smoke.json \
      --corpus examples/demo-corpus.json --allow-demo \
      --report "$report_dir/results.json" --summary "$report_dir/summary.md" || status=$?
    if [[ -f "$report_dir/summary.md" && -n "${VERIFY_SUMMARY_FILE:-}" ]]; then
      cat "$report_dir/summary.md" >> "$VERIFY_SUMMARY_FILE"
    fi
    exit "$status"
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
