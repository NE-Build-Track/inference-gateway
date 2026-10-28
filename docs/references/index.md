# References and provenance

API contracts checked on 2026-10-25. The locked SDK versions and executable tests define
the initial verified subset; upstream APIs can evolve independently.

- [OpenAI Chat Completions request contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI streaming event contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [Anthropic Messages contract](https://platform.claude.com/docs/en/api/messages)
- [Anthropic streaming event contract](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Harness engineering](https://openai.com/index/harness-engineering/): repository knowledge,
  progressive disclosure, mechanical boundaries, and feedback loops. User-supplied local
  copy: `~/Downloads/openai-harness-engineer.md`.

The user requested these local skill sources under `~/git-repos/ai-skills/`:

- `python-dependency-injection-skill`: Pure DI, Protocol ports, and explicit ownership.
- `domain-driven-design-skill`: local vocabulary and translation boundaries.
- `designing-data-intensive-applications-skill`: statelessness, partial failure, and restraint.
- `enterprise-integration-patterns-skill`: request/reply, translation, and correlation.
- `the_sense_of_style-writing-skill`: task-oriented documentation for developers and agents.

Their guidance is reflected in [design decisions](../design-docs/decisions.md). Local
skill files are authoring inputs, not runtime dependencies. The repository remains
usable without those paths. This implementation is an application of their guidance,
not an endorsement by the books' authors.

Extended gateway contracts checked on 2026-10-26 against the installed OpenAI and Anthropic
SDK request/response classes, including their streaming accumulators. See the
[feature contract](../product-specs/extended-generation.md) and
[dated Livepeer probe evidence](../exec-plans/livepeer-capability-gaps.md).

- [Models/Responses candidate build](models-responses-build.json): local image identity and source hashes.
- [OpenAI model catalog](https://developers.openai.com/api/reference/resources/models/methods/list).
- [Responses request contract](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
- [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events).

- [Repository compatibility review](repository-compatibility-2026-10-27.md): pins, newer M3 compatibility and discovery deployment fix.
- [M3 payment compatibility review](repository-compatibility-2026-10-28.md): new Core payment ownership, blocked native upgrade and required port.
