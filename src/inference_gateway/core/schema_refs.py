"""Bounded, document-local JSON Schema reference expansion; never performs I/O."""

import re
from urllib.parse import unquote

from inference_gateway.core.models import JSON


def resolve_schema(schema: dict[str, JSON]) -> dict[str, JSON]:
    remaining = 4096

    def target(reference: JSON) -> dict[str, JSON]:
        if not isinstance(reference, str) or not reference.startswith("#"):
            raise ValueError("Only document-local schema references are supported")
        if re.search(r"%(?![0-9a-fA-F]{2})", reference):
            raise ValueError("Invalid schema reference encoding")
        pointer = unquote(reference[1:], errors="strict")
        if pointer and not pointer.startswith("/"):
            raise ValueError("Schema references require JSON Pointers")
        value: JSON = schema
        for token in pointer[1:].split("/") if pointer else []:
            if re.search(r"~(?![01])", token):
                raise ValueError("Invalid JSON Pointer escape")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict) and token in value:
                value = value[token]
            elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
                if len(token) > 6 or int(token) >= len(value):
                    raise ValueError("Missing schema reference target")
                value = value[int(token)]
            else:
                raise ValueError("Missing schema reference target")
        if not isinstance(value, dict):
            raise ValueError("Schema reference target must be an object")
        return value

    def visit(node: dict[str, JSON], active: tuple[int, ...], depth: int) -> dict[str, JSON]:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise ValueError("Schema reference expansion exceeds limit")
        if id(node) in active:
            raise ValueError("Recursive schema references are unsupported")
        active = (*active, id(node))
        for key in ("$defs", "definitions"):
            if key in node:
                definitions = node[key]
                if not isinstance(definitions, dict):
                    raise ValueError("Schema definitions must be an object")
                for definition in definitions.values():
                    if not isinstance(definition, dict):
                        raise ValueError("Schema definition must be an object")
                    visit(definition, active, depth + 1)
        if "$ref" in node:
            if node.keys() - {"$ref", "$defs", "definitions", "title", "description"}:
                raise ValueError("Unsupported schema reference siblings")
            resolved = visit(target(node["$ref"]), active, depth + 1)
            return {**resolved, **{k: node[k] for k in ("title", "description") if k in node}}
        result = {k: v for k, v in node.items() if k not in ("$defs", "definitions")}
        properties = node.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {
                key: visit(child, active, depth + 1) if isinstance(child, dict) else child
                for key, child in properties.items()
            }
        items = node.get("items")
        if isinstance(items, dict):
            result["items"] = visit(items, active, depth + 1)
        branches = node.get("anyOf")
        if isinstance(branches, list):
            result["anyOf"] = [
                visit(child, active, depth + 1) if isinstance(child, dict) else child
                for child in branches
            ]
        return result

    return visit(schema, (), 0)
