#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
fixture_image="knowledge-assistant-api:refresh-fixture-$$"
work="$(mktemp -d examples/.refresh-fixture.XXXXXX)"
cleanup() {
  local status=$?
  docker image rm "$fixture_image" >/dev/null 2>&1 || true
  rm -rf "$work"
  return "$status"
}
trap cleanup EXIT
# Synthetic test-only data exercises the real-corpus branch. Never publish this image.
python3 - "$work/corpus.json" <<'PY'
import json
import sys
from pathlib import Path
corpus = json.loads(Path('examples/demo-corpus.json').read_text())
corpus.update(isDemo=False, snapshotId='fixture-refresh-v1', version='refresh-fixture')
for document in corpus['documents']:
    document['source']['snapshotId'] = corpus['snapshotId']
target = Path(sys.argv[1])
target.write_text(json.dumps(corpus))
target.chmod(0o600)  # Match the file created by the ingestion command.
PY
docker buildx build --load --build-arg "CORPUS_FILE=$work/corpus.json" \
  --tag "$fixture_image" .
bash scripts/smoke-image.sh "$fixture_image" "$work/corpus.json"
