# M3 payment compatibility review

Review the latest NE Build Track releases against the gateway's native and validated
contracts. Preserve original request/response bytes, protocol headers, cancellation,
usage accounting and explicit failure outcomes. Do not silently substitute a dependency
that drops native transport or introduces additional paid retries.

Acceptance:

- Compare fetched Core, SDK, node and Clearinghouse revisions with current pins.
- Run gateway checks on its pinned dependencies and isolate a latest-M3 compatibility run.
- Fail startup clearly if the installed Core lacks the native contract.
- Record payment/failover and accounting migration requirements before repinning.
- Keep development services stopped, preserve volumes and do not publish images.

A latest-M3 upgrade is blocked if native body/header/error streaming is absent. Retain the
working pin in that case; document the exact required dependency work rather than claim
that latest M3 is supported.

## Outcome

Fetched NE branches; the material change is Core `efb9c03`, with a new engine-owned paid
transport but no native contract. Its isolated full gateway run passed 473 tests and
failed 27 native cases. Added and exercised a startup guard against that actual revision,
and regression tests for incomplete request/response contracts and valid route modes.
Retained working pins. Full locked checks pass 506 tests. The
[review report](../../references/repository-compatibility-2026-10-28.md) specifies the
Core PR #55 port, payment/failover decisions and exact upgrade acceptance work.

Review and gateway guard are complete. The dependency upgrade remained blocked on that
Core port at the time. The port merged later that day; the
[upstream repin plan](../active/upstream-repin-publication.md) carries the upgrade.
