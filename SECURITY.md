# Security policy

## Reporting vulnerabilities

Use [GitHub private vulnerability reporting](https://github.com/NE-Build-Track/inference-gateway/security/advisories/new)
for suspected vulnerabilities. Do not open a public issue containing exploit details, keys,
prompts or customer information. If private reporting is unavailable, contact a repository
maintainer through their GitHub profile and request a private reporting channel before
sending sensitive details.

Include affected versions/commit, deployment mode, reproduction steps, impact and any
suggested mitigation. Use disposable test credentials and redact secrets. Maintainers will
triage reports and coordinate disclosure; this volunteer project does not promise response
or remediation deadlines.

## Supported versions

The project is pre-1.0. Security fixes target the current default branch. There is no
long-term-support or historical-version maintenance commitment. Use a reviewed, pinned
revision and monitor repository advisories.

## Deployment boundaries

Local fake mode may intentionally have no authentication. Public deployments should use
registry authentication, TLS termination, restricted management interfaces and appropriate
request/concurrency limits. The key-management CLI trusts filesystem access to the accounting
database; treat it as administrative access. Secrets and state backups require access controls.

Keep gateway retail records and backend payment records separate. Unknown wholesale costs
are not zero, and an interrupted inference request may already have incurred payment.
Model outputs and tool-call arguments are untrusted inputs to client applications; this
gateway does not execute tools or validate an application's authorization decisions.
