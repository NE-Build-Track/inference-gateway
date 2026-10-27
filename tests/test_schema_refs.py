import copy
import json

import httpx
import pytest

from inference_gateway.app import create_app
from inference_gateway.core.schema_refs import resolve_schema
from inference_gateway.core.structured import schema_example, validate_strict_schema, validate_value


def referenced_schema():
    return {
        "type": "object",
        "properties": {
            "first": {"$ref": "#/$defs/a~1b~0c"},
            "second": {"$ref": "#/definitions/alias"},
            "items": {"type": "array", "items": {"$ref": "#/$defs/a~1b~0c"}},
        },
        "required": ["first", "second", "items"],
        "additionalProperties": False,
        "$defs": {"a/b~c": {"anyOf": [{"type": "string", "enum": ["yes"]}, {"type": "null"}]}},
        "definitions": {"alias": {"$ref": "#/$defs/a~1b~0c"}},
    }


def test_shared_chained_escaped_refs_validate_without_mutation():
    schema = referenced_schema()
    original = copy.deepcopy(schema)
    validate_strict_schema(schema)
    validate_value({"first": "yes", "second": None, "items": ["yes", None]}, schema)
    with pytest.raises(ValueError):
        validate_value({"first": "no", "second": None, "items": []}, schema)
    validate_value(schema_example(schema), schema)
    assert schema == original


def test_root_reference_and_percent_encoded_pointer():
    schema = {"$ref": "#/$defs/my%20object", "$defs": {"my object": referenced_schema()}}
    # References are document-relative, not relative to the referenced definition.
    schema["$defs"]["my object"] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    validate_strict_schema(schema)
    assert schema_example(schema) == {}


@pytest.mark.parametrize(
    "ref",
    [
        "https://example.com/schema",
        "other.json#/x",
        "#anchor",
        "#/$defs/missing",
        "#/$defs/a~2b",
        "#/%GG",
        "#/$defs/value/type",
    ],
)
def test_invalid_references(ref):
    with pytest.raises(ValueError):
        schema_example({"$ref": ref, "$defs": {"value": {"type": "string"}}})


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}},
        {"type": "object", "properties": {"child": {"$ref": "#"}}},
        {"type": "string", "$defs": {"unused": {"$ref": "#/$defs/unused"}}},
        {"$ref": "#/$defs/a", "$defs": {"a": {"type": "string"}}, "enum": ["x"]},
    ],
)
def test_cycles_and_constraint_siblings_rejected(schema):
    with pytest.raises(ValueError):
        schema_example(schema)


def test_expansion_budget():
    definitions = {"leaf": {"type": "string"}}
    previous = "leaf"
    for i in range(13):
        definitions[str(i)] = {
            "type": "object",
            "properties": {
                "a": {"$ref": f"#/$defs/{previous}"},
                "b": {"$ref": f"#/$defs/{previous}"},
            },
        }
        previous = str(i)
    with pytest.raises(ValueError, match="exceeds limit"):
        resolve_schema({"$ref": f"#/$defs/{previous}", "$defs": definitions})


@pytest.mark.parametrize("stream", [False, True])
async def test_structured_output_reference_fake(stream):
    schema = referenced_schema()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/responses",
            json={
                "model": "fake-model",
                "input": "answer",
                "stream": stream,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "answer",
                        "schema": schema,
                        "strict": True,
                    }
                },
            },
        )
    assert response.status_code == 200, response.text
    result = (
        response.json()
        if not stream
        else [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ][-1]["response"]
    )
    validate_value(json.loads(result["output"][0]["content"][0]["text"]), schema)


async def test_recursive_schema_rejected_before_backend():
    from test_strict_tools import body, scripted_backend

    backend, engine = await scripted_backend(True, {})
    request = body()
    request["model"] = "gemma"
    request["tools"][0]["parameters"] = {"$ref": "#"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as client:
        response = await client.post("/v1/responses", json=request)
    assert response.status_code == 400
    assert not engine.jobs.requests


def test_referenced_objects_must_satisfy_strict_rules():
    schema = {
        "$ref": "#/$defs/object",
        "$defs": {"object": {"type": "object", "properties": {"value": {"type": "string"}}}},
    }
    with pytest.raises(ValueError, match="Strict objects"):
        validate_strict_schema(schema)
