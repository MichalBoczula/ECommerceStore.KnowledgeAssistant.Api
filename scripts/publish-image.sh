#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

image="${1:?Usage: scripts/publish-image.sh IMAGE REPOSITORY APP_KEY}"
repository="${2:?Missing Docker Hub repository}"
app_key="${3:?Missing Terraform app_images key}"
: "${GITHUB_SHA:?Missing source commit}"
: "${GITHUB_REPOSITORY:?Missing source repository}"
: "${SCANNED_IMAGE_ID:?Record the scanned image before publishing}"

[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$SCANNED_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]
case "$app_key" in knowledge_api|knowledge_web) ;; *) echo 'Unsupported Terraform app key' >&2; exit 1 ;; esac
sha_tag="$repository:$GITHUB_SHA"
latest_tag="$repository:latest"
test "$SCANNED_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$image")"
docker tag "$image" "$sha_tag"
docker tag "$image" "$latest_tag"
test "$SCANNED_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$sha_tag")"
test "$SCANNED_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$latest_tag")"

docker push "$sha_tag" | tee "$RUNNER_TEMP/push-sha.log"
docker push "$latest_tag" | tee "$RUNNER_TEMP/push-latest.log"
sha_digest="$(awk '/digest: sha256:/ { print $3 }' "$RUNNER_TEMP/push-sha.log" | tail -n 1)"
latest_digest="$(awk '/digest: sha256:/ { print $3 }' "$RUNNER_TEMP/push-latest.log" | tail -n 1)"
[[ "$sha_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
test "$sha_digest" = "$latest_digest"
reference="docker.io/$repository@$sha_digest"
mkdir -p artifacts
jq -n --arg image "$reference" --arg sha "$GITHUB_SHA" --arg source "$GITHUB_REPOSITORY" \
  --arg id "$SCANNED_IMAGE_ID" --arg key "$app_key" --arg sha_tag "$sha_tag" --arg latest "$latest_tag" \
  '{schemaVersion: 1, image: $image, sourceCommit: $sha, sourceRepository: $source,
    scannedImageId: $id, tags: [$sha_tag, $latest], terraform: {app_images: {($key): $image}}}' \
  > artifacts/image-reference.json
{
  printf 'Published `%s` and `%s` from scanned image `%s`.\n\n' "$sha_tag" "$latest_tag" "$SCANNED_IMAGE_ID"
  printf 'Terraform image reference: `%s`\n\n' "$reference"
  printf 'Download the `image-reference` artifact for the complete reference. Add `%s` to the existing `app_images` map; preserve its other entries.\n\n' "$app_key"
  printf 'Deployment uses ECommerceStore.Terraform and its existing Azure Storage backend. This workflow publishes the image only.\n'
} >> "$GITHUB_STEP_SUMMARY"
