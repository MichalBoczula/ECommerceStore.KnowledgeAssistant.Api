"""LiveDocs v1 schemas plus snapshot and source-registration invariants."""

import re
from importlib.resources import files
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from .json_source import loads


def validate_schema(value: object, name: str) -> None:
    schema = loads(files(__package__).joinpath(f"schemas/{name}.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    if next(validator.iter_errors(value), None) is not None:
        raise ValueError(f"Invalid LiveDocs {name} schema")


def validate_archive_url(value: str) -> None:
    url = urlparse(value)
    if (
        url.scheme != "https"
        or not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", url.netloc)
        or not url.path.startswith("/livedocs/reports/")
        or not url.path.endswith(".zip")
        or url.query
        or url.fragment
        or "%" in url.path
        or "\\" in url.path
        or any(p in {"", ".", ".."} for p in url.path[1:].split("/"))
    ):
        raise ValueError("Expected a credential-free LiveDocs Azure Blob URL")


def select_projects(manifest: dict, version: str | None) -> tuple[str, list[dict]]:
    validate_schema(manifest, "portal")
    versions = [v["version"] for v in manifest["versions"]]
    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate versions in portal manifest")
    if manifest["latestVersion"] not in versions:
        raise ValueError("Latest version does not exist")
    selected_version = version or manifest["latestVersion"]
    selected = next((v for v in manifest["versions"] if v["version"] == selected_version), None)
    if not selected or not selected["projects"]:
        raise ValueError("Selected version is empty or does not exist")
    projects = selected["projects"]
    if len({p["project"] for p in projects}) != len(projects):
        raise ValueError("Duplicate projects in selected snapshot")
    for reference in projects:
        validate_archive_url(reference["artifact"]["url"])
    return selected_version, sorted(projects, key=lambda p: p["project"])


def registered_repository(registry: dict, project: str) -> str:
    if not isinstance(registry, dict) or not isinstance(registry.get(project), dict):
        raise ValueError("Selected project is absent from the LiveDocs registration")
    repository = registry[project].get("repository")
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("Invalid LiveDocs repository registration")
    return repository
