# Users, usage and accounting

The gateway supports multiple users and API keys in one process. Set
`GATEWAY_ACCOUNTING_DB` to a durable SQLite file to enable registry authentication and
accounting. Compose defaults to `/data/gateway.db`. The CLI and HTTP server use the same
file. The CLI is the administrative boundary; no public key-creation endpoint exists.

## Identity and migration

A user can own several keys. Each key has a stable ID, name, SHA-256 hash, permissions,
creation time, optional expiry, and revocation flag. Secrets contain 256 random bits and
are shown only when issued or rotated. They are not saved in the database or logs.

| Permission | Access |
| --- | --- |
| `inference` | Generate through both API formats |
| `reports` | Read the owner's requests, usage and retail charges across their keys |
| `admin` | Inference and reporting across users, including wholesale observations |

A reporting key is user-scoped, not limited to its own key ID. Filters never broaden its
access. Revocation and user disabling take effect on the next request; an admitted
request can finish. Rotation atomically issues a replacement and revokes its predecessor.
Historical records retain their original user and key IDs.

Registry mode rejects anonymous requests and cannot be combined with `GATEWAY_API_KEY`.
When `GATEWAY_ACCOUNTING_DB` is absent, the original shared-key/anonymous development
mode remains available, without reporting endpoints. Existing anonymous history is not
retroactively assigned to users. Existing Builder Core jobs and payment state are retained.
The shared upstream payment allocation is independent of gateway user identities.

## Retail is independent of wholesale

Retail charges use the gateway's price version captured before dispatch. Input, cached
input, and output rates are per million tokens. Missing cached rates use the input rate.
A later price change never changes an existing request's rate. Cached input is a subset
of total input, not an additional token count. Missing cache details are treated as no
reported cache discount. Thinking output is not supported by the current inference contract.

The charge is:

```text
((input - cached_input) * input_rate
 + cached_input * cached_rate
 + output * output_rate) / 1,000,000
```

Calculations use Decimal, rounded per request to nine decimal places with half-even
rounding. Reports sum those recorded charges, not rounded display values. Currencies
are grouped separately; there is no implicit currency conversion. Gateway report amounts
are decimal strings. OpenAI cost reports use JSON numbers; use gateway reports for exact
financial arithmetic. Anthropic cost amounts are decimal strings in USD cents.

Verified usage is billable even if the client disconnects. Usage is persisted before the
Livepeer adapter awaits completion/reporting, so cancellation during those awaits does
not erase it. Invalid requests and missing retail prices are rejected before dispatch.
A dispatched request without authoritative usage remains pending; it is not assigned an
invented count or charge. A backend observation confirming no payment and no measured
usage marks the request not billable. Confirmed usage can subsequently resolve it.

Charges and adjustments are append-only. A unique constraint permits one base charge per
request. Repeated identical usage writes are harmless; conflicting usage requires an
explicit adjustment. Adjustment operation IDs prevent duplicate credits. This is accounting,
not customer payment collection: there are no invoices, wallets or card charges.

Wholesale is a separate backend observation. The backend-neutral `AccountingSource`
provides a job reference, cost status, amount/currency, usage quantities with their source,
and attempt evidence. A backend that cannot report cost returns `unavailable`. Fake
accounting explicitly reports fixture zero cost; its token counts are synthetic.

Livepeer reads Builder Core's public job and cost APIs. Its cost basis is **ticket expected
value**, not final redemption settlement. Engine usage marked `app_reported` is identified
as such; it is not relabeled as independently measured backend consumption. Paid failures
and uncertain attempts remain visible even if there is no retail revenue. A missing job
is `unknown`; absent or pending monetary evidence is null, never zero.

The gateway stores dated wholesale projections, while Builder Core remains their authority.
An administrative margin is calculated only for rows with both costs in the same currency;
unresolved rows are counted separately. Reports cover attributed gateway requests, not the
entire shared allocation or unattributed spend from other applications.

## Durable lifecycle and recovery

Request records separate generation outcome, measured usage, retail billing and wholesale
status. They do not store prompts, generated content, tool arguments or raw backend errors.
The gateway database uses WAL, foreign keys, migrations and short transactions. One process
holds a file lock for its lifetime; a second server fails instead of recovering active work.
CLI operations remain available while the server runs.

On startup, unfinished local requests become `interrupted`. The reconciliation loop reads
backend evidence and retries accounting operations only. It never redispatches inference.
A job reference is saved as soon as the Livepeer stream opens. If a crash happens earlier,
the adapter searches the public engine job list by the request's operation reference.
Unknown records remain unresolved; discovery is not proof that a job never ran.

The loop checks due rows every five seconds, up to 100 per pass. Successful projections
refresh after 60 seconds, allowing late corrections. Backend reads are bounded to ten
seconds per request. Errors back off exponentially, then pause after eight failures for
operator redrive. The request record exposes failure count, next reconciliation time and
last successful observation time to administrators. A failed usage report is retried by
reading the engine's recorded usage before writing, so inference is never repeated.

There is no transaction spanning SQLite and the backend. Crashes can leave incomplete
knowledge; the system retains that uncertainty and repairs it from evidence. Client
retries are separate generation requests: this version does not provide client idempotency
or response replay. Disable automatic client retries when paid uncertainty must not cause
another generation. Back up the whole gateway data volume with the service stopped,
including both the gateway and engine databases.

## Reporting API

| GET endpoint | Result |
| --- | --- |
| `/v1/organization/usage/completions` | OpenAI-shaped usage buckets |
| `/v1/organization/costs` | OpenAI-shaped retail cost buckets |
| `/v1/organizations/usage_report/messages` | Anthropic-shaped usage buckets |
| `/v1/organizations/cost_report` | Anthropic-shaped retail cost buckets |
| `/v1/gateway/requests` | Request records and exact retail ledger entries |
| `/v1/gateway/requests/{request_id}` | One authorized request; 404 for another user's record |
| `/v1/gateway/usage` | Usage buckets with gateway grouping options |
| `/v1/gateway/costs` | Exact decimal retail cost buckets |
| `/v1/gateway/wholesale` | Admin-only wholesale observations and comparable margins |

These are supported subsets of provider reporting APIs, not full organization-management
implementations. Both authentication header styles work for reporting. Inference retains
its protocol-specific headers. Standard completion/message responses contain token usage;
monetary costs are retrieved separately.

Supply `start_time`/`end_time` as UTC Unix seconds, or `starting_at`/`ending_at` as timezone-aware
ISO timestamps. The interval is inclusive/exclusive, attributed by request start time.
Adjustments appear in their original request's bucket. End defaults to the current second
plus one. Use `bucket_width=1m`, `1h`, or `1d`; cost reports use `1d`. Windows are limited to
31 days, 10,000 buckets and 10,000 authorized requests; narrow the window for larger exports.

`limit` is 1–1000, default 100. For bucket reports it counts buckets; for request lists it
counts requests. Follow `next_page` using the same query and `page`. Cursors preserve the
end time and pagination position, but do not freeze accounting against later reconciliation.
`X-Gateway-Accounting-As-Of` identifies report generation time. Request records expose the
actual wholesale observation time; it may be older. Responses use `Cache-Control: no-store`.

List filters accept repeated plain names or `[]`: `api_key_ids`, `user_ids`, `models`,
`outcomes`, `project_ids`, and `workspace_ids`. Projects and workspaces map to gateway user
IDs; they do not create separate tenant entities. Grouping uses repeated `group_by` or
`group_by[]`:

- OpenAI usage: `api_key_id`, `user_id`, `project_id`, `model`.
- OpenAI costs: `api_key_id`, `user_id`, `project_id`, `line_item`.
- Anthropic usage: `api_key_id`, `workspace_id`, `model`.
- Anthropic costs: `workspace_id`, `description`.
- Gateway aggregates additionally support `outcome` and all the dimensions above.

`line_item` and `description` identify the public model. Unknown parameters/groupings are
rejected. Usage aggregates sum known measurements and carry `gateway_unresolved_requests`.
Provider cost results omit groups with no known charge, and bucket-level unresolved counts
retain those requests. Native records show null amounts and explicit billing states.

The installed OpenAI SDK supports `client.admin.organization.usage.completions(...)` and
`.costs(...)`; construct it with `admin_api_key` as well as the gateway `base_url`. A key
with `reports` may use these methods for its own user. Anthropic reporting can be called
with HTTP; the pinned Anthropic Python SDK has no reporting resource. The HTTP contracts
are grounded in the official [OpenAI usage reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions)
and [Anthropic usage/cost guide](https://platform.claude.com/docs/en/manage-claude/usage-cost-api).
