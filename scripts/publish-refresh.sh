#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:?Usage: scripts/publish-refresh.sh IMAGE}"
: "${GITHUB_SHA:?Missing source commit}"
: "${GITHUB_RUN_ID:?Missing run ID}"
: "${GITHUB_RUN_ATTEMPT:?Missing run attempt}"
: "${SCANNED_IMAGE_ID:?Missing scanned image ID}"
: "${REFRESH_RELEASE_ID:?Missing verified release identity}"
[[ "${GITHUB_REF:-}" == refs/heads/main ]]
[[ "${GITHUB_REPOSITORY:-}" == MichalBoczula/ECommerceStore.KnowledgeAssistant.Api ]]
[[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch || "${GITHUB_EVENT_NAME:-}" == schedule ]]
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ && "$REFRESH_RELEASE_ID" =~ ^[0-9a-f]{64}$ ]]
[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ && "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]]
[[ "$SCANNED_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]
test "$SCANNED_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$image")"
test "$REFRESH_RELEASE_ID" = "$(docker image inspect --format '{{index .Config.Labels "io.ecommerce.knowledge.release"}}' "$image")"
tag="mb0101/ecommerce-store-knowledge-assistant-api:refresh-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
docker tag "$image" "$tag"
test "$SCANNED_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$tag")"
docker push "$tag" | tee "$RUNNER_TEMP/refresh-push.log"
digest="$(awk '/digest: sha256:/ { print $3 }' "$RUNNER_TEMP/refresh-push.log" | tail -n 1)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
reference="docker.io/mb0101/ecommerce-store-knowledge-assistant-api@$digest"
mkdir -p artifacts/refresh
jq -n --arg image "$reference" --arg sha "$GITHUB_SHA" --arg id "$SCANNED_IMAGE_ID" \
  --arg release "$REFRESH_RELEASE_ID" --arg tag "$tag" \
  '{schemaVersion: 1, image: $image, sourceCommit: $sha, scannedImageId: $id,
    releaseId: $release, tag: $tag}' > artifacts/refresh/image.json
printf 'Published refresh candidate `%s`. Download `knowledge-refresh-handoff` for the matching Terraform values.\n' \
  "$reference" >> "$GITHUB_STEP_SUMMARY"
