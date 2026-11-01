#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# First-party release, fixed version and SHA-256; no runtime dependency added.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl --fail --silent --show-error --location --max-time 60 \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz \
  --output "$work/actionlint.tar.gz"
echo "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  $work/actionlint.tar.gz" | sha256sum --check --status
tar --extract --gzip --file "$work/actionlint.tar.gz" --directory "$work" actionlint
"$work/actionlint" .github/workflows/*.yml
