"""Catch accidental outward dependencies as adapters and use cases evolve."""

import ast
from pathlib import Path

ALLOWED = {
    "domain": {"domain"},
    "application": {"application", "domain"},
    "infrastructure": {"infrastructure", "application", "domain"},
}


def test_layer_imports_point_inward():
    for layer, allowed in ALLOWED.items():
        for path in Path("src/knowledge_assistant", layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else (
                        [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
                    )
                )
                for module in modules:
                    if module.startswith("knowledge_assistant."):
                        assert module.split(".")[1] in allowed, f"{path} imports {module}"
                    if layer in {"domain", "application"}:
                        assert module.split(".")[0] not in {"fastapi", "azure", "openai"}, path
