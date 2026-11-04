from inference_gateway.app import create_app


def test_existing_conversation_schemas_have_complete_local_references():
    schema = create_app().openapi()
    assert schema["openapi"] == "3.1.0"
    for path in ("/v1/chat/completions", "/v1/responses", "/v1/messages"):
        operation = schema["paths"][path]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert "text/event-stream" in operation["responses"]["200"]["content"]
        assert operation["security"]

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                target = schema
                for part in value["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
