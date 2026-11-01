"""Strict JSON and offline schema validation; schema-valid is not business-rule proof."""

import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from openapi_schema_validator import OAS30Validator
from referencing import Registry
from referencing.exceptions import Unresolvable


def parse_example(text: str):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate member in generated example")
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError("Non-finite number in generated example")

    if len(text.encode()) > 24000:
        raise ValueError("Generated example exceeds the size budget")
    result = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    json.dumps(result, allow_nan=False)
    return result


def validate_example(payload: dict, example) -> bool | None:
    if not isinstance(payload.get("requestSchema", {}), dict):
        return None
    validator_type = (
        OAS30Validator
        if str(payload.get("openapiVersion", "")).startswith("3.0")
        else Draft202012Validator
    )
    schema = {
        **payload.get("requestSchema", {}),
        "components": {"schemas": payload.get("referenceSchemas", {})},
    }
    try:
        json.dumps(example, allow_nan=False)
        validator_type.check_schema(schema)
        validator = validator_type(
            schema, registry=Registry(), format_checker=validator_type.FORMAT_CHECKER
        )
        return next(validator.iter_errors(example), None) is None
    except (SchemaError, Unresolvable, RecursionError, ValueError, TypeError):
        return None


def resolve_example(payload: dict, generated: str | None) -> tuple:
    from .evidence_catalog import documented_example

    documented = documented_example(payload)
    example, origin, valid, notes = payload.get("exampleRequest"), "unavailable", None, []
    available = documented
    if documented:
        origin = "openapi"
    elif generated is not None:
        try:
            example, origin, available = parse_example(generated), "generated", True
        except (ValueError, RecursionError):
            notes.append("A generated request was invalid JSON and was omitted.")
            valid = False
    if available:
        valid = validate_example(payload, example)
        if valid is not True:
            if documented:
                notes.append(
                    "The documented example does not satisfy its request schema."
                    if valid is False
                    else "The documented example could not be verified against its request schema."
                )
            else:
                example, origin = None, "unavailable"
                notes.append(
                    "A generated request failed schema validation and was omitted."
                    if valid is False
                    else "A generated request could not be verified and was omitted."
                )
    elif not notes and (payload.get("mediaType") or payload.get("requestSchema")):
        notes.append("No request example is available for " + payload["path"])
    if origin == "generated":
        notes.append(
            "The generated example is schema-checked; business-rule compliance and "
            "successful API execution are not established."
        )
    incomplete = origin == "unavailable" and bool(notes) or available and valid is not True
    return example, origin, valid, notes, incomplete
