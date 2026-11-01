"""Which models each SDK may see, and the list format that SDK parses."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Family = Literal["openai", "anthropic", "systemone"]

# A native model is listed only if it serves every endpoint the caller's SDK can reach. One OpenAI
# SDK covers chat and Responses, and a list request does not say which the caller will use.
FAMILY_ENDPOINTS: Mapping[Family, tuple[str, ...]] = {
    "openai": ("chat_completions", "responses"),
    "anthropic": ("messages",),
    "systemone": ("systemone",),
}
# The gateway translates validated routes to these. Nothing translates decisions.
TRANSLATED_ENDPOINTS = frozenset({"chat_completions", "responses", "messages"})

# Matches the catalog's `created: 0`: the date is unknown, not 1970.
UNKNOWN_DATE = "1970-01-01"


@dataclass(frozen=True)
class Listing:
    name: str
    display_name: str
    description: str = ""
    release_date: str = UNKNOWN_DATE


def caller_family(headers: Mapping[str, str]) -> Family:
    """Select a view, never access: a forged header only reshapes what the key may already see."""
    if "anthropic-version" in headers:
        return "anthropic"
    if "x-typesafe-sdk" in headers:
        return "systemone"
    return "openai"


def render(family: Family, listings: Iterable[Listing], owned_by: str) -> dict[str, Any]:
    rows = tuple(listings)
    if family == "systemone":
        return {
            "models": [
                {"name": r.name, "description": r.description, "release_date": r.release_date}
                for r in rows
            ]
        }
    if family == "anthropic":
        return {
            "data": [
                {
                    "type": "model",
                    "id": r.name,
                    "display_name": r.display_name,
                    "created_at": f"{r.release_date}T00:00:00Z",
                }
                for r in rows
            ],
            "has_more": False,
            "first_id": rows[0].name if rows else None,
            "last_id": rows[-1].name if rows else None,
        }
    return {
        "object": "list",
        "data": [
            {"id": r.name, "object": "model", "created": 0, "owned_by": owned_by} for r in rows
        ],
    }
