# Operations integration contract

Integrated in `server.py` / `web.py`: `OperationsServer` starts the read-only worker
in its HTTP ASGI lifespan, wires MCP tools and authenticated browser routes. The
configuration example includes the required scopes. Execution queues retain their
separate worker/tick contract; real advertising writes remain disabled by default.

`baidu_ads_mcp.operations` is an independent companion to the existing change store.
It never calls advertising writes or external messaging APIs.

## Lifecycle

```python
operations = Operations(change_store, policy_store)
worker = BackgroundWorker(operations, resolve_actor, refresh)
stop = asyncio.Event()
task = asyncio.create_task(worker.run(stop))
# ASGI shutdown:
stop.set()
await task
```

`resolve_actor(subject)` must be an **async server-owned** identity resolver. It
must recheck the core identity and current grants and return a `Principal`; a value
supplied by an MCP client is never sufficient. The worker requires exactly the
initiating subject/version, verifies every action's scope/account and rechecks the
identity after asynchronous I/O. A resolver that cannot verify identity must raise
`QueryError('UNAUTHORIZED')` or `IDENTITY_BRIDGE_UNAVAILABLE`, not return a cached
indefinitely valid principal. Do not save a raw bearer token/password in a schedule.
A persisted non-secret core identity assertion/version can support refresh checks;
its lifetime must not exceed the authorizing grant. If using current OAuth tokens
as evidence, no unexpired grant means scheduled work must stop until reauthorized.

`refresh(actor)` is an async callback importing only into MCP's own cache and
returning a small JSON result (e.g. release_id). It must never start core collection.
It should have its own timeout, since each background job has a 25-second ceiling.
The parent updates its active `query` and `change_store.query` after import succeeds.

All stored timestamps are UTC epoch seconds; calendar windows are computed in
Asia/Shanghai. Daily jobs cover completed dates only. Missed periods coalesce into
one latest window. Claims use 60-second leases with fencing; a crashed worker can
be recovered after expiry. Read/import/export retries are capped at three attempts.
Paused or expired subscriptions suppress in-flight notification publication. No
subscription can contain arbitrary code, advertising operations or an external recipient.

## MCP registration

- `baidu_ads_create_export`: `ExportInput` + a 16–128 character idempotency key;
  call `operations.enqueue_export(actor, key, **params)` and return job_id.
- `baidu_ads_get_job`: dispatch known background IDs to `operations.job` (legacy
  import-job IDs can still use the prior store). Scope is checked by job kind.
- `baidu_ads_request_refresh`: `operations.enqueue_refresh(actor, key)`.
- `baidu_ads_create_subscription`: `SubscriptionInput`; account daily summary,
  existing observation/decision-rule evaluation or MCP cache refresh only.
- `baidu_ads_get_subscription`, `baidu_ads_pause_subscription`: subscription_id.
- `baidu_ads_get_notifications`: `operations.inbox(actor, limit=50)` (max 200).
- `baidu_ads_mark_notification_read`: notification_id.

Add `exports:create` and `subscriptions:manage` to the server scopes and applicable
trusted grants. Viewer scopes may include exports and in-app summaries; creation
of observation policies still requires the existing `policies:manage` operator role.
No method accepts an arbitrary identity, URL or SQL statement from a caller.

## Authenticated download

The job result contains `export_id` and a relative `/exports/<id>` download path.
The web route must call the existing **web session** verifier, then
`operations.download(actor, export_id)`. Return the bytes with its MIME type and:

```text
Content-Disposition: attachment; filename="<returned filename>"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

Do not allow anonymous access or place a bearer token in a URL. A change to the
identity version revokes old downloads. Exports expire after one hour; the store
allows 100 exports, 50 MiB aggregate content, 10 MiB per export and 10,000 rows.
Over-limit requests fail rather than silently truncate. JSON preserves source
text and all coverage metadata; CSV prefixes potential formula cells with an
apostrophe and reports that encoding in export metadata. No private raw inputs or
plaintext CLI/WeCom counts are written by this exporter.

The queued export pins the release ID at enqueue. If the active release changed
before execution, it fails with `SNAPSHOT_CHANGED` instead of silently returning
another revision. Retry uses one deterministic export ID and cannot create
unbounded duplicate content. Standalone `create_export` is useful for tests only;
interactive service registration should enqueue exports so large generation does
not block the event loop.

## Tests

```sh
services/mcp/.venv/bin/python -m unittest discover -s services/mcp/tests -p test_operations.py -v
```

Unit tests and HTTP integration tests cover export values/coverage, owner/version checks, formulas, size
and row bounds, expiry, durable job idempotency, stale-worker fencing, bounded
retry/error redaction, Beijing dates, pauses, policy/account constraints, private
inbox, async export completion and current-identity verification.

## Separate fixed automatic rules

`automatic_rules.py` is separate from observation/recommendation rules and ordinary
subscriptions. It supports explicit pause, resume, fixed campaign budget and fixed
manual bid templates for named account/object IDs. It requires the independent
owner-only `policies:automate` scope, which is **not** present in example grants.
The configuration's `mutations_enabled` and `automatic_mutations_enabled` both
default to false. Creation, updates and simulation do not grant execution authority.

The `/automatic-rules/{id}` owner page checks current identity, Origin and CSRF,
then approves the exact version/hash, action targets, money/action limits, cadence,
expiry and stop conditions. Updates and pauses invalidate approval. Each approved
rule has at most one immutable batch per version and complete Beijing date;
`interval_seconds` controls polling, not permission to create extra daily batches.
The ASGI lifespan polls due rules every 30 seconds; it only enqueues the ordinary
execution worker and never calls provider writes itself.

Each batch persists the original release, date window and metrics, reserves limits
atomically, and pins a normal approved plan to the rule hash. Idempotent retries
recover the same plan/job; they never create a second send intent. Current identity
and all automatic scopes, approval, account/object/action limits, both switches,
capabilities, money limits and live preconditions must still hold at submission.
Plans expire within 30 minutes and before the next Beijing midnight. A prior unknown
result blocks every version of the same rule until normal read-only reconciliation
resolves it. Existing execution-job pages and manual ID reconciliation apply.

Historical simulation uses historical performance and **current snapshot settings**
as an explicit assumption when checking fixed targets and monetary deltas. It is
not a historical execution backtest. It creates no plans, reservations or writes.
`test_automatic_rules.py`, `test_automatic_http.py` and `test_review_automatic.py`
exercise only synthetic providers, including the actual HTTP MCP -> owner webpage
-> queue -> readback path and concurrent revoke/update/cache-swap boundaries.
