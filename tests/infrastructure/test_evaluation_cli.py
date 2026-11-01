import json
import sys
from pathlib import Path

import pytest

from knowledge_assistant.cli import main
from knowledge_assistant.infrastructure.evaluation import command


def invoke(monkeypatch, tmp_path, *args):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "evaluate",
            "--suite",
            "evaluation/demo-smoke.json",
            "--corpus",
            "examples/demo-corpus.json",
            "--allow-demo",
            "--report",
            str(output),
            *args,
        ],
    )
    main()
    return json.loads(output.read_text())


def test_default_is_offline_even_when_azure_modes_are_in_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("KA_RETRIEVAL_MODE", "azure")
    monkeypatch.setenv("KA_GENERATION_MODE", "azure")

    def forbidden(*a, **kw):
        pytest.fail("Offline evaluation constructed a cloud client")

    monkeypatch.setattr(command, "AzureRetriever", forbidden)
    monkeypatch.setattr(command, "ModelPlanner", forbidden)
    report = invoke(monkeypatch, tmp_path, "--summary", str(tmp_path / "summary.md"))
    assert report["passed"] == 4
    assert "4/4 passed" in (tmp_path / "summary.md").read_text()
    assert report["corpusFileSha256"] and report["suiteFileSha256"]


def test_failed_expectation_returns_one_and_publishes_failed_report(monkeypatch, tmp_path):
    s = json.loads(Path("evaluation/demo-smoke.json").read_text())
    s["cases"][0]["expect"]["contracts"][0]["method"] = "DELETE"
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(s))
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, tmp_path, "--suite", str(path))
    assert error.value.code == 1
    r = json.loads((tmp_path / "report.json").read_text())
    assert r["failed"] == 1 and "contracts" in r["cases"][0]["failures"]


@pytest.mark.parametrize(
    "change", ["pin", "missing_report", "demo_opt_in", "output", "unknown_case"]
)
def test_invalid_setup_returns_two_without_cloud_calls_or_overwrite(monkeypatch, tmp_path, change):
    def forbidden(*a, **kw):
        pytest.fail("Invalid setup constructed a cloud client")

    monkeypatch.setattr(command, "AzureRetriever", forbidden)
    monkeypatch.setattr(command, "ModelPlanner", forbidden)
    s = json.loads(Path("evaluation/demo-smoke.json").read_text())
    c = json.loads(Path("examples/demo-corpus.json").read_text())
    if change == "pin":
        s["sourcePins"][0]["commitSha"] = "b" * 40
    if change == "missing_report":
        s["isDemo"] = c["isDemo"] = False
    sp = tmp_path / "suite.json"
    sp.write_text(json.dumps(s))
    cp = tmp_path / "corpus.json"
    cp.write_text(json.dumps(c))
    output = tmp_path / "report.json"
    output.write_text("previous report")
    args = [
        "knowledge",
        "evaluate",
        "--suite",
        str(sp),
        "--corpus",
        str(cp),
        "--report",
        str(output),
        "--retrieval-mode",
        "azure",
        "--generation-mode",
        "azure",
    ]
    if change != "demo_opt_in":
        args.append("--allow-demo")
    if change == "output":
        args.extend(["--report", str(sp)])
    if change == "unknown_case":
        args.extend(["--case-id", "absent"])
    previous = sp.read_text()
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert sp.read_text() == previous and output.read_text() == "previous report"


def test_case_filter_bounds_provider_work(monkeypatch, tmp_path):
    report = invoke(monkeypatch, tmp_path, "--case-id", "contract")
    assert report["total"] == report["passed"] == 1
    assert report["cases"][0]["id"] == "contract"


def test_resources_close_after_provider_setup_failure(monkeypatch, tmp_path):
    closed = []

    class Retriever:
        def __init__(self, *a):
            pass

        def close(self):
            closed.append(True)

    def unavailable(*a):
        raise ValueError("private-setting")

    monkeypatch.setattr(command, "AzureRetriever", Retriever)
    monkeypatch.setattr(command, "ModelPlanner", unavailable)
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, tmp_path, "--retrieval-mode", "azure", "--generation-mode", "azure")
    assert error.value.code == 2 and closed == [True]
    assert not (tmp_path / "report.json").exists()
