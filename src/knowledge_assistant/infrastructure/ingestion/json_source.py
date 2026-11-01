"""Strict JSON inputs with safe public diagnostics (no input/provider values)."""

import json
from pathlib import Path


def loads(value: str | bytes):
    def invalid_constant(_):
        raise ValueError("Non-finite number in documentation source")

    def unique(pairs):
        output = {}
        for key, item in pairs:
            if key in output:
                raise ValueError("Duplicate JSON member in documentation source")
            output[key] = item
        return output

    return json.loads(value, object_pairs_hook=unique, parse_constant=invalid_constant)


def read(path: Path):
    return loads(path.read_text(encoding="utf-8-sig"))
