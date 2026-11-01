#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source_image="${1:?Provide the already built CI image}"
container="knowledge-refresh-fixture-$$"
fixture_image="knowledge-assistant-api:refresh-fixture-$$"
work="$(mktemp -d)"
cleanup() {
  local status=$?
  docker rm -f "$container" >/dev/null 2>&1 || true
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
Path(sys.argv[1]).write_text(json.dumps(corpus))
PY
docker create --name "$container" "$source_image" >/dev/null
docker cp "$work/corpus.json" "$container:/app/data/corpus.json"
docker commit "$container" "$fixture_image" >/dev/null
bash scripts/smoke-image.sh "$fixture_image" "$work/corpus.json"
