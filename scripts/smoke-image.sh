#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:?Usage: scripts/smoke-image.sh IMAGE}"
name="knowledge-api-smoke-$$"
work="$(mktemp -d)"
key=knowledge-smoke-only-000000000000
cleanup() {
  local status=$?
  if (( status != 0 )); then docker logs "$name" >&2 || true; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
  return "$status"
}
trap cleanup EXIT
docker run -d --name "$name" -p 127.0.0.1::8080 \
  -e KA_RETRIEVAL_MODE=local -e KA_GENERATION_MODE=evidence -e "KA_API_KEY=$key" "$image" >/dev/null
base="http://$(docker port "$name" 8080/tcp)"
for attempt in {1..30}; do
  if curl --fail --silent --max-time 3 "$base/health/live" -o /dev/null; then break; fi
  sleep 1
done
curl --fail --silent --show-error --max-time 10 "$base/health/live" -o "$work/live.json"
curl --fail --silent --show-error --max-time 10 "$base/health/ready" -o "$work/ready.json"
status="$(curl --silent --show-error --max-time 10 "$base/api/v1/catalog" \
  -o "$work/problem.json" -w '%{http_code}')"
test "$status" = 401
curl --fail --silent --show-error --max-time 10 -H "X-API-Key: $key" \
  "$base/api/v1/catalog" -o "$work/catalog.json"
python3 - "$work" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
load = lambda name: json.loads((root / name).read_text())
assert load('live.json')['status'] == 'healthy'
assert load('ready.json')['status'] == 'ready'
problem = load('problem.json')
assert problem['status'] == 401 and problem['code'] == 'unauthorized' and problem['traceId']
catalog = load('catalog.json')
assert catalog['schemaVersion'] == 1 and catalog['answerMode'] == 'evidence'
assert catalog['snapshots'][0]['isDemo'] is True
(root / 'request.json').write_text(json.dumps({
    'question': 'Explain mobile phone validation and give me the JSON contract to create a mobile phone',
    'service': 'products', 'snapshotId': catalog['snapshots'][0]['id'],
}))
PY
curl --fail --silent --show-error --max-time 30 -H "X-API-Key: $key" \
  -H 'Content-Type: application/json' --data-binary "@$work/request.json" \
  "$base/api/v1/query" -o "$work/answer.json"
python3 - "$work/answer.json" <<'PY'
import json
import sys
from pathlib import Path

answer = json.loads(Path(sys.argv[1]).read_text())
assert answer['status'] == 'answered' and answer['isDemo'] is True
assert answer['answerMode'] == 'evidence' and answer['retrievalMode'] == 'local_keyword'
assert {s['type'] for s in answer['sections']} >= {'validation', 'api_contract'}
contract = next(s for s in answer['sections'] if s['type'] == 'api_contract')
assert contract['requestSchema'] and contract['exampleSchemaValid'] is True
assert contract['sourceIds'] and answer['sources']
PY
echo "Health, API-key enforcement and sourced synthetic answer passed for $image"
