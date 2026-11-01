import json
import os
import subprocess
from pathlib import Path

import pytest


def publish(tmp_path, **overrides):
    tools = tmp_path / "tools"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text("""#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$*" == *io.ecommerce.knowledge.release* ]]; then echo "$REFRESH_RELEASE_ID"
elif [[ "$1 $2" == "image inspect" ]]; then echo "$FAKE_IMAGE_ID"
elif [[ "$1" == push ]]; then
 digest=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
 echo "refresh: digest: sha256:$digest size: 1"
fi
""")
    docker.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(tools) + os.pathsep + os.environ["PATH"],
        GITHUB_SHA="a" * 40,
        GITHUB_REF="refs/heads/main",
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REPOSITORY="MichalBoczula/ECommerceStore.KnowledgeAssistant.Api",
        GITHUB_RUN_ID="123",
        GITHUB_RUN_ATTEMPT="1",
        SCANNED_IMAGE_ID="sha256:" + "c" * 64,
        FAKE_IMAGE_ID="sha256:" + "c" * 64,
        REFRESH_RELEASE_ID="d" * 64,
        FAKE_DOCKER_LOG=str(tmp_path / "commands"),
        RUNNER_TEMP=str(tmp_path),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
    )
    env.update(overrides)
    # Isolate this script's output files from the checkout and other tests.
    script = Path("scripts/publish-refresh.sh").read_text().replace('cd "$(dirname "$0")/.."', "")
    result = subprocess.run(
        ["bash", "-c", script, "fixture", "candidate-image"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, tmp_path / "commands"


def test_refresh_publishes_the_scanned_image_with_no_latest_or_main_sha_overwrite(tmp_path):
    result, log = publish(tmp_path)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    pushes = [x for x in lines if x.startswith("push ")]
    assert pushes == ["push mb0101/ecommerce-store-knowledge-assistant-api:refresh-123-1"]
    assert "latest" not in log.read_text()
    receipt = json.loads((tmp_path / "artifacts/refresh/image.json").read_text())
    assert receipt["sourceCommit"] == "a" * 40 and receipt["scannedImageId"] == "sha256:" + "c" * 64
    assert receipt["image"].endswith("@sha256:" + "b" * 64)


@pytest.mark.parametrize(
    "overrides",
    [
        {"GITHUB_REF": "refs/heads/feature"},
        {"GITHUB_EVENT_NAME": "pull_request"},
        {"GITHUB_REPOSITORY": "fork/Api"},
        {"SCANNED_IMAGE_ID": "sha256:" + "e" * 64},
        {"REFRESH_RELEASE_ID": "not-a-hash"},
    ],
)
def test_invalid_publication_context_or_changed_image_never_pushes(tmp_path, overrides):
    result, log = publish(tmp_path, **overrides)
    assert result.returncode != 0
    assert not log.exists() or "push " not in log.read_text()
    assert not (tmp_path / "artifacts/refresh/image.json").exists()
