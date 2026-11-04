# Keep ledger work off the event loop

Every gateway request, stream and health check shares one event loop, and every SQLite call in the
gateway runs on it. A slow read stops all of them. An operator found the worst case: on a copy of
their ledger with 7,919 requests, one report read took 2.1 seconds, and a dashboard polling reports
made the gateway hang. A synthetic ledger of the same size froze the loop for 7.2 seconds per report
page.

The report read had three faults. The only index on `entries` is partial (`kind='charge'`), so each
request's entry lookup scanned the whole table. Every page loaded the entire window before filtering
and slicing. And the read ran on the event loop.

The same audit found two slower, constant costs. Reconciliation refreshes every request every 60
seconds forever, and each paid request makes five synchronous SQLite transactions, which take 12 to
42 ms depending on the disk.

The work shipped in three pull requests, smallest risk first, and is released in v0.2.3.

## Acceptance

### 1. Report reads

- A ledger at schema version 3 gains `entries(request_id, created, id)` and
  `requests(created, id)` on startup and moves to version 4. Older gateways refuse version 4.
- Every report read uses an index; no query plan scans `requests` or `entries`.
- Request lists filter and page in SQL. A cursor resumes after the last `(created, id)` returned,
  so pages never repeat or skip a request. Cursors issued before this change still work.
- Bucket reports read only the rows in their page's buckets, and return the same buckets a single
  full read would. The 10,000-request cap applies to one page, not to the whole window.
- Reports run in worker threads, at most two at once. The event loop keeps running during a read.
- Response shapes do not change.

### 2. Reconciliation

- A request stops being refreshed once its cost is final, after a bounded number of later checks
  for corrections. Requests with no job back off and stop, as failures already do.
- Finding a job by request ID searches only jobs created around the request's own time.

### 3. Request-path writes and logs

- Ledger writes leave the event loop through one writer thread, preserving order. A cancelled
  request still records its outcome.
- Native and System One requests log completion and errors with request ID and elapsed time, and
  never bodies.

## Work

- [x] Migration to schema version 4 with both indexes.
- [x] SQL filters and positional pages for request lists; page-range reads for bucket reports.
- [x] Reports in worker threads, two at a time.
- [x] Tests: migration, query plans, paging, bucket equivalence, the per-page cap and the event
  loop.
- [x] Reconciliation slows with age and stops for settled and long-unresolved requests.
- [x] The job search without a saved job ID covers only jobs near admission time.
- [x] Tests: the schedule, settling, operator redrive, a draining backlog and the bounded search.
- [x] Writer thread for request-path and reconciliation writes.
- [x] Native completion and error logs.
- [x] Tests: order, cancelled waits, logged failures, a responsive loop under slow writes, a
  request cancelled during its begin, a cancelled stream, and the native log.

## Verification

Synthetic ledger with 7,919 requests and 7,523 charges, on NVMe:

| Read | Before | After |
| --- | --- | --- |
| One request-list page of 100 | 7.2 s, with the loop frozen throughout | 0.015 s |
| All 7,919 requests in pages of 100 | About 580 s | 0.48 s |
| Daily usage for the whole window | About 7.3 s, the same full read | 0.19 s |
| Longest event-loop freeze during any read | 7.2 s | 0.026 s |

`make check` passes 672 tests. The operator's own reproduction has not been run against
this change.

Reconciliation, by the same schedule: a request is refreshed about 60 times in its first day, then
stops once settled. Before, each was refreshed every minute forever, so past about 1,200 requests the
loop never idled. On upgrade, every settled request older than a day gets one last check and stops.
At 100 per pass with five seconds between passes, 7,919 of them take 80 passes, at least seven
minutes. The bounded job
search reads one page where it used to read every job; a test with 5,002 jobs covers it.

Request-path writes, with the ledger on a slow SSD and 100 requests four at a time: the median
event-loop freeze fell from 44 ms to 0.2 ms, and the longest from 103 ms to 7 ms. Throughput did not
change, since the disk still sets it.

## Rollback

Redeploying v0.2.2 or earlier fails at startup, because those versions refuse schema version 4. To roll back,
stop the gateway, back up the database, then remove the indexes and restore the version:

```sh
sqlite3 retail.db "DROP INDEX entry_lookup; DROP INDEX report_window; PRAGMA user_version=3;"
```
