#!/usr/bin/env python3
"""Portable refresh checks; credentials stay in environment variables."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.delivery.refresh import (
    enforce_budget,
    handoff,
    prepare_plan,
    select_publication,
    validate_receipt,
    verify_release,
)
from knowledge_assistant.infrastructure.ingestion.json_source import read
from knowledge_assistant.infrastructure.retrieval.indexing_io import write_report


def outputs(values):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select")
    select.add_argument("--livedocs-commit", default="")
    select.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--selection", type=Path, required=True)
    prepare.add_argument("--publication", type=Path, required=True)
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--suite", type=Path, default=Path("evaluation/products-v1.json"))
    prepare.add_argument("--output", type=Path, required=True)
    budget = sub.add_parser("budget")
    budget.add_argument("--index-plan", type=Path, required=True)
    budget.add_argument("--max-inputs", type=int, default=2000)
    budget.add_argument("--max-input-bytes", type=int, default=8_000_000)
    budget.add_argument("--max-vector-bytes", type=int, default=40_000_000)
    cached = sub.add_parser("cached")
    cached.add_argument("--plan", type=Path, required=True)
    cached.add_argument("--receipt", type=Path, required=True)
    for name in ["verify", "handoff"]:
        command = sub.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--ingestion-report", type=Path, required=True)
        command.add_argument("--index-report", type=Path, required=True)
        command.add_argument("--retrieval-report", type=Path, required=True)
        command.add_argument("--model-report", type=Path, required=True)
        command.add_argument("--suite", type=Path, default=Path("evaluation/products-v1.json"))
        if name == "handoff":
            command.add_argument("--image", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        if args.command == "select":
            result = select_publication(os.environ["GITHUB_SHA"], args.livedocs_commit)
            write_report(args.output, result)
            outputs(
                {
                    "livedocs_sha": result["livedocsCommit"],
                    "run_id": result["publicationRun"],
                    "artifact_id": result["publicationArtifact"],
                }
            )
        elif args.command == "prepare":
            result = prepare_plan(
                read(args.selection),
                read(args.publication),
                args.source,
                args.suite,
                Settings(),
                os.environ["KA_LIVEDOCS_BASE_URL"],
                os.environ.get("KA_REFRESH_REVISION", "1"),
            )
            write_report(args.output, result)
            outputs({"release_id": result["releaseId"]})
        elif args.command == "budget":
            enforce_budget(
                read(args.index_plan), args.max_inputs, args.max_input_bytes, args.max_vector_bytes
            )
        elif args.command == "cached":
            receipt = read(args.receipt)
            validate_receipt(read(args.plan), receipt)
            # Ensure the immutable candidate is still pullable before reusing its handoff.
            subprocess.run(
                ["docker", "manifest", "inspect", receipt["image"]],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        else:
            plan = read(args.plan)
            verified = verify_release(
                plan,
                args.corpus,
                args.ingestion_report,
                read(args.index_report),
                read(args.retrieval_report),
                read(args.model_report),
                args.suite,
                Settings(),
            )
            if args.command == "handoff":
                result = handoff(plan, verified, read(args.image))
                write_report(args.output, result)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        p.exit(
            1,
            "Refresh verification failed. Check publication/source pins, configuration, "
            "budgets and matching successful reports.\n",
        )
    print(json.dumps({"stage": args.command, "verified": True}))


if __name__ == "__main__":
    main()
