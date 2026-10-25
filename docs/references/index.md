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
