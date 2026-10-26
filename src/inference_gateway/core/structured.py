"""Small, explicit JSON Schema subset shared by validation and deterministic fixtures.

No references, remote schemas, coercion, or silently ignored keywords.
"""

import math

from inference_gateway.core.models import JSON


def reject_nonfinite(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def schema_example(schema: dict[str, JSON], depth: int = 0) -> JSON:
    if depth > 16:
        raise ValueError("Schema nesting exceeds limit")
    allowed = {
        "type",
        "description",
        "title",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
    }
    if schema.keys() - allowed:
        raise ValueError("Unsupported JSON Schema keyword")
    kind = schema.get("type")
    if kind not in ("object", "array", "string", "integer", "number", "boolean", "null"):
        raise ValueError("Schema requires a supported type")
    for key in ("title", "description"):
        if key in schema and not isinstance(schema[key], str):
            raise ValueError("Invalid schema annotation")
    object_keys = {"properties", "required", "additionalProperties"}
    if kind != "object" and schema.keys() & object_keys:
        raise ValueError("Object keywords require object type")
    if kind != "array" and "items" in schema:
        raise ValueError("Items requires array type")
    value: JSON
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError("Invalid object schema")
        if any(not isinstance(k, str) or k not in properties for k in required):
            raise ValueError("Required properties must be defined")
        if len(required) != len(set(str(k) for k in required)):
            raise ValueError("Duplicate required property")
        if "additionalProperties" in schema and not isinstance(
            schema["additionalProperties"], bool
        ):
            raise ValueError("Only boolean additionalProperties is supported")
        value = {}
        for key, child in properties.items():
            if not isinstance(child, dict):
                raise ValueError("Property schema must be an object")
            example = schema_example(child, depth + 1)
            if key in required:
                value[key] = example
    elif kind == "array":
        child = schema.get("items")
        if not isinstance(child, dict):
            raise ValueError("Array requires items schema")
        schema_example(child, depth + 1)
        value = []
    else:
        defaults: dict[str, JSON] = {
            "string": "fake",
            "integer": 0,
            "number": 0.0,
            "boolean": False,
            "null": None,
        }
        value = defaults[str(kind)]
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise ValueError("Enum must be nonempty")
        base = {k: v for k, v in schema.items() if k != "enum"}
        for candidate in values:
            validate_value(candidate, base)
        value = values[0]
    return value


def validate_value(value: JSON, schema: dict[str, JSON]) -> None:
    kind = schema["type"]
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value),
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if not valid[str(kind)]:
        raise ValueError("Output does not match schema type")
    if "enum" in schema:
        values = schema["enum"]
        assert isinstance(values, list)
        if not any(type(value) is type(v) and value == v for v in values):
            raise ValueError("Output is outside enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        assert isinstance(properties, dict) and isinstance(required, list)
        if any(k not in value for k in required if isinstance(k, str)):
            raise ValueError("Missing required output property")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError("Unexpected output property")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                validate_value(item, child)
    if isinstance(value, list):
        child = schema["items"]
        assert isinstance(child, dict)
        for item in value:
            validate_value(item, child)
