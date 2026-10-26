"""Capabilities required by a command, including history that must not disappear."""

from inference_gateway.core.models import (
    Document,
    Generate,
    HostedResult,
    Media,
    Reasoning,
    Text,
    ToolCall,
)


def required_features(request: Generate) -> frozenset[str]:
    features: set[str] = set()
    if any(
        value is not None
        for value in (
            request.sampling.top_p,
            request.sampling.top_k,
            request.sampling.frequency_penalty,
            request.sampling.presence_penalty,
            request.sampling.seed,
        )
    ):
        features.add("sampling")
    if request.sampling.stop:
        features.add("stop")
    if request.reasoning_budget is not None:
        features.add("reasoning_budget")
    if (
        request.reasoning
        or request.reasoning_effort is not None
        or request.reasoning_budget is not None
    ):
        features.add("reasoning")
    if request.output_format:
        features.add("structured_output")
    if request.parallel_tools:
        features.add("parallel_tools")
    if request.hosted_search:
        features.add("hosted_tools")
    if request.sources:
        features.add("citations")
    if request.output_media:
        features.add("media_output")
    for message in request.messages:
        for part in message.content:
            if isinstance(part, Media):
                features.add("media_input")
            if isinstance(part, Reasoning):
                features.add("reasoning")
            if isinstance(part, Document) or isinstance(part, Text) and part.citations:
                features.add("citations")
            if isinstance(part, HostedResult) or isinstance(part, ToolCall) and part.hosted:
                features.add("hosted_tools")
    return frozenset(features)
