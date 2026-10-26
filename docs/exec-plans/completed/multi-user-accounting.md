# Multi-user identity and accounting

Status: completed 2026-10-26. One gateway process; multiple users and keys.

## Contract

Gateway SQLite owns users, key hashes, request attribution, immutable price versions,
measured retail usage and append-only charges/adjustments. Backend accounting remains
wholesale authority; gateway snapshots are explicitly dated derived reports. Missing
usage/cost is never manufactured as zero. No accounting retry dispatches inference.

Keys are issued by CLI, scoped to inference, own-user reports, or administration.
Registry mode rejects anonymous and legacy shared keys. Existing shared-key mode remains
an explicit compatibility option without multi-user reporting.

Verified usage is billable even after disconnection. Missing usage remains unresolved;
rejected requests are not charged. Prices are captured before dispatch. Money uses exact
Decimal arithmetic and nine decimal places, half-even rounding, separately by currency.

## Steps and acceptance

1. Domain contracts, versioned price calculation, SQLite migration and key CLI.
2. Durable request tracking and idempotent charge creation around validated generation.
3. Backend-neutral accounting lookup, Livepeer integration, restart reconciliation.
4. Authorized gateway reports plus OpenAI/Anthropic usage and cost adapters.
5. Black-box identity/isolation/reporting tests, recovery tests and live reconciliation.
6. Operator guide, builds and complete checks.

No replicas, distributed workers, customer payment collection, or inference replay cache.

## Verification and decisions

- 129 offline tests passed; strict mypy, Ruff, format and documentation checks passed.
- Wheel/source distributions and the non-root Docker image built successfully.
- Real HTTP black-box harness provisions keys/prices through CLI and verifies both protocols,
  exact retail arithmetic, two users/three inference keys, scopes, rotation/revocation,
  price changes and restart persistence. No server imports or database reads in that suite.
- Live run: 14 successful generation requests; usage matched request records and provider
  reports. All wholesale records resolved to positive observed costs with matching attempt
  sums and app-reported usage. Post-restart public API checks preserved single ledger entries.
- Snapshot for that run: retail USD 0.001459000; wholesale ticket expected value USD
  0.001564182350730314. These are distinct books, not a markup calculation or final settlement.
- Reconciliation is bounded and backed by durable due rows. Eight consecutive failures pause
  a row for CLI redrive. Missing usage/job evidence remains explicit, never an inference retry.
- Native costs use decimal strings; OpenAI monetary projections use JSON numbers and Anthropic
  uses USD cents. Unsupported parameters/currencies are rejected rather than silently changed.
- Compose now enables registry mode; host startup retains explicit legacy mode when the
  accounting path is unset. Bootstrap uses CLI-issued secrets and no public creation route.
- CLI-generated live acceptance/operator secrets remain in ignored mode-0600 local storage.

Limits: single process, no customer collection, no client replay/idempotency, no automatic
currency conversion, no full-allocation reconciliation across other applications. Reports
cap windows at 31 days and 10,000 requests. Wholesale is backend evidence, not redemption
settlement. See the debt tracker for production quotas, exports and operations automation.
