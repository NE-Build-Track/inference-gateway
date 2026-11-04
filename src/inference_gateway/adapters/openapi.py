"""Describe manually parsed conversational bodies without changing runtime validation."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from inference_gateway.adapters.response_api import ResponsesRequest
from inference_gateway.adapters.schemas import AnthropicRequest, OpenAIRequest


def attach_conversation_schemas(app: FastAPI) -> None:
    def document() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, routes=app.routes, openapi_version="3.1.0"
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        security = schema["components"].setdefault("securitySchemes", {})
        security.update(
            HTTPBearer={"type": "http", "scheme": "bearer"},
            AnthropicKey={"type": "apiKey", "in": "header", "name": "x-api-key"},
        )
        for path, model in (
            ("/v1/chat/completions", OpenAIRequest),
            ("/v1/messages", AnthropicRequest),
            ("/v1/responses", ResponsesRequest),
        ):
            name = model.__name__
            body = model.model_json_schema(ref_template=f"#/components/schemas/{name}__{{model}}")
            for key, value in body.pop("$defs", {}).items():
                components[name + "__" + key] = value
            components[name] = body
            operation = schema["paths"][path]["post"]
            operation["security"] = [
                {"AnthropicKey" if path == "/v1/messages" else "HTTPBearer": []}
            ]
            if path == "/v1/messages":
                operation.setdefault("parameters", []).append(
                    {
                        "in": "header",
                        "name": "anthropic-version",
                        "required": True,
                        "schema": {"type": "string", "const": "2023-06-01"},
                    }
                )
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                },
            }
            operation["responses"]["200"].setdefault("content", {})["text/event-stream"] = {
                "schema": {"type": "string"},
                "description": "Provider-shaped SSE events; supported subset only.",
            }
        app.openapi_schema = schema
        return schema

    app.openapi = document  # type: ignore[method-assign]
