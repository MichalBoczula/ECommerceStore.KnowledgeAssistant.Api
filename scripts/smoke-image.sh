#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:?Usage: scripts/smoke-image.sh IMAGE}"
expected_corpus="${2:-examples/demo-corpus.json}"
test -f "$expected_corpus"
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
docker cp "$name:/app/data/corpus.json" "$work/baked.json" >/dev/null
cmp "$expected_corpus" "$work/baked.json"
cp "$expected_corpus" "$work/expected.json"
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
corpus = load('expected.json')
assert catalog['snapshots'][0]['isDemo'] == corpus['isDemo']
assert catalog['snapshots'][0]['id'] == corpus['snapshotId']
(root / 'request.json').write_text(json.dumps({
    'question': 'Explain mobile phone validation and give me the JSON contract to create a mobile phone',
    'service': 'products', 'snapshotId': catalog['snapshots'][0]['id'],
}))
PY
curl --fail --silent --show-error --max-time 30 -H "X-API-Key: $key" \
  -H 'Content-Type: application/json' --data-binary "@$work/request.json" \
  "$base/api/v1/query" -o "$work/answer.json"
python3 - "$work" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
answer = json.loads((root / 'answer.json').read_text())
corpus = json.loads((root / 'expected.json').read_text())
assert answer['status'] in {'answered', 'partial'} and answer['isDemo'] == corpus['isDemo']
assert answer['snapshotId'] == corpus['snapshotId'] and answer['service'] == 'products'
assert answer['answerMode'] == 'evidence' and answer['retrievalMode'] == 'local_keyword'
assert {s['type'] for s in answer['sections']} >= {'validation', 'api_contract'}
contract = next(s for s in answer['sections'] if s['type'] == 'api_contract')
assert contract['requestSchema']
if corpus['isDemo']:
    assert answer['status'] == 'answered' and contract['exampleSchemaValid'] is True
assert contract['sourceIds'] and answer['sources']
sources = {d['source']['id']: d['source'] for d in corpus['documents']}
assert all(source == sources[source['id']] for source in answer['sources'])
original = next(d['payload'] for d in corpus['documents'] if d['kind'] == 'api_contract'
                and d['source']['id'] in contract['sourceIds'])
assert contract['operationId'] == original['operationId']
assert contract['method'] == original['method'] and contract['path'] == original['path']
assert contract['requestSchema'] == original['requestSchema']
PY
echo "Exact baked corpus, health, API-key enforcement and sourced answer passed for $image"
