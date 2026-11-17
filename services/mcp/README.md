# Baidu Ads MCP — development implementation

Independent implementation of [the full plan](../../docs/MCP_PLAN.md). **Not deployed
or production accepted.** The service now has 46 MCP tools, OAuth-protected
Streamable HTTP, an independent human approval site, and a local stdio mode.
The official [MCP Python SDK v1.26.0](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0)
provides the protocol and OAuth handlers. Custom code checks resource, role,
account, identity version, proposal version and CSRF boundaries.

Core requirements, imports, login, collector, publication and startup tasks remain
unchanged. The optional [core-side reader](../core-reader/README.md) is a separate
process/package/environment. Neither service runs automatically.

## Implemented and tested locally

| Tools | Behavior |
|---|---|
| `list_accounts`, `get_metric_catalog`, `get_data_status` | Account capabilities, units, fixed revision/hashes/observed dates; live collector health remains unknown |
| `get_summary`, `get_performance` | 12 advertising report families including device/region/hour/creative/channel; exact filters, metric sorting and signed pagination; summary reconciles account and dashboard keyword totals |
| `get_website_metrics` | Site-wide exact-range totals, additive daily fallback, account-wide paid goal sessions including Tina install-copy |
| `get_entities`, `get_settings` | Snapshot inventory by ID/name; shadow variants retained; paused schedule semantics documented |
| `compare_periods`, `analyze_performance` | Comparable complete periods and high-cost evidence for human intent review |
| `get_cli_metrics`, `get_wecom_metrics` | Authenticated, fixed-release private aggregates through core reader; no keys copied into MCP |
| `create_change_plan`, `get_change_plan`, `preview_change_plan`, `update_change_plan` | Typed proposals, saved diffs, version checks; edits invalidate prior approval |
| `request_change_approval`, `cancel_change_plan`, `get_change_history` | Human approval URL, cancellation, own/owner lifecycle audit |
| `execute_change_plan` | Versioned approval and idempotent execution queue; capability, fresh identity and live precondition checks; **default `MUTATIONS_DISABLED`** |
| `request_refresh`, `get_job` | Durable background import jobs with recovery; hashes and complete-release validation; retain last good; never trigger core collection |
| `create_policy`, `get_policy`, `pause_policy`, `evaluate_policy` | Persist and manually evaluate observation-only account spend/minimum-click rules; deduplicate per rule/revision/range |
| `get_capabilities`, `get_execution_job`, `create_compensation_plan` | Verified per-account operation contracts, per-item outcome/readback, separately reviewed compensation drafts |
| `create_export` | Durable CSV/JSON export jobs; authenticated browser download, one-hour expiry, size and row bounds |
| `create_subscription`, `get_subscription`, `pause_subscription`, `get_notifications`, `mark_notification_read` | Beijing-time summaries, rule evaluations and cache refresh; private in-app notifications |
| `create_decision_rule`, `get_decision_rule`, `pause_decision_rule`, `simulate_policy`, `evaluate_decision_rule` | Typed observation or pause recommendations for explicit IDs; historical simulation and unapproved drafts; never automatic execution |
| `create_automatic_rule`, `update_automatic_rule`, `get_automatic_rule`, `pause_automatic_rule`, `simulate_automatic_rule`, `trigger_automatic_rule` | Separately preapproved fixed pause/resume/budget/manual-bid rules; explicit money/count/cooldown/expiry limits and shared execution checks; both runtime switches default off |

All tools have the `baidu_ads_` prefix. State-changing tools are annotated as such;
advertising execution requires a separately configured adapter, approved capabilities and the operator switch. The supplied configuration disables both execution switches and includes no enabled provider capability.
Stdio defaults to a local viewer; private sources, proposals and policies require a
configured authenticated HTTP service. The OS user's selected release copy is the
stdio trust boundary, not dashboard login.

The typed operation contracts include campaign budgets; manual bids; campaign,
adgroup, keyword and creative pauses; exact/phrase negatives; regions, paused
schedule intervals and device configuration; keyword matching; campaign bid
strategy/target prices; campaign/adgroup/keyword/creative creation; creative
text/URLs; and separately permissioned deletion. New objects start paused.
Each account/operation needs its own current provider evidence, enum values,
precondition fields and monetary limits. An implemented HTTP contract is **not**
proof the production token has that permission. Unknown or expired contracts deny.

The official Baidu SDK contract reference is pinned in `baidu_write.py`. Local
wire-contract tests use mock HTTP responses; no live advertising writes were made.
Creation records its request intent before sending and persists returned IDs. A
lost creation response without authoritative provenance remains unknown and locked;
matching a name or an owner-entered candidate ID does not prove creation. There is
no blind resubmission. Create parent objects first, reconcile their IDs, then submit
separately reviewed child plans; unresolved parents cannot be used as known IDs.
Delete cannot be compensated as if reversible. Compensations for reversible changes
are new drafts and must still match the last applied state before execution.

## Install and test independently

From `services/mcp`, Python 3.11+ and uv:

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -c "import sys; sys.path.insert(0, 'tests'); from fixtures import make_release; print(make_release('../../work/mcp-fixture'))"
uv run --frozen baidu-ads-mcp --release-copy ../../work/mcp-fixture/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

The fixture is explicitly synthetic and stored under ignored `work/`. A real input
copy has three HTML files under a 32-lowercase-hex release directory:
`report/index.html`, `report/yakshaver/index.html`, `report/ssw/index.html`.
Do not add production reports, identity material, cache or state to this public repo.

For HTTP development, create a private JSON configuration under ignored `work/`,
using the shape in `http-config.example.json`. Replace the sample clients and grants
with intentionally configured identities. Start the separately configured core reader,
then set `MCP_BRIDGE_SERVICE_TOKEN` in the MCP process environment through private
configuration (not a command line argument or repository file). From this directory:

```sh
uv run --frozen baidu-ads-mcp --release-copy ../../work/mcp-fixture/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --http-config ../../work/mcp-http.json
```

The process binds **127.0.0.1 only**. `loopback_development: true` allows local HTTP
and non-secure development cookies; otherwise HTTPS URLs and secure `__Host-`
cookies are required. Production private ingress, independent host, certificate,
OS constraints and account configuration have not been provisioned or approved.

The HTTP routes are `/mcp`, standard OAuth discovery/authorize/token/revoke routes,
`/login`, `/logout`, `/consent`, `/plans`, `/plans/{id}`, `/execution/{id}`,
`/exports/{id}`, `/notifications`, `/subscriptions` and `/automatic-rules/{id}`. Clients are statically
registered with exact redirects. Dynamic registration is disabled. Both authorize
and token requests require the exact `/mcp` resource. Authorization requires PKCE
S256; access lasts 15 minutes, refresh up to 30 days with rotation and reuse-family
revocation, web sessions one hour. Opaque tokens are stored as hashes only. Core
identity is rechecked for every authenticated request; unavailable bridge denies
access. Local grant changes require process restart and invalidate old grants via
version checks. No owner/operator role can be supplied through a tool argument.

Example summary arguments:

```json
{"params":{"account":"tinacms","start":"2026-11-01","end":"2026-11-07"}}
```

Example **proposal only** arguments (fictional entity ID):

```json
{"params":{"account":"ssw","operations":[{"op":"set_campaign_budget","entity_id":"123","amount":"120.00"}],"reason":"请审核日预算上限调整"}}
```

## Data and isolation boundaries

- Import is capped at 32 MiB per HTML, 200,000 rows per advertising dimension.
  All three sources must validate before exposure. Queries allow 366 dates,
  pages default to 50/max 200. Release and cursor expiry is one hour; refresh
  imports a complete source revision into MCP's own directory. Old cursors are
  invalid after refresh. A stopped/failed reader never changes core publication.
- Refresh, export and subscription jobs run in the independent HTTP service's
  background worker, with leases, fencing and bounded retries. Opt-in private
  `auto_refresh` configuration polls every five minutes and backs off on failure.
  Each job rechecks current identity, grants and scopes; revoked authority stops it.
- An import failure preserves the existing in-memory snapshot and cache pointer.
  Expired snapshots still fail closed; old data is never relabelled live. Private
  source errors do not erase advertising data. Cache retains the current revision
  and two predecessors within a 384 MiB import budget; export content has a separate
  50 MiB bound and one-hour expiry. OS disk quotas remain a deployment requirement.
- Money/ratios are decimal JSON strings; CTR is a fraction. Missing fields are
  null, absent dates never assumed zero, zero denominators null. Source labels
  remain unchanged and are untrusted content, not instructions. Identity grouping
  uses IDs, not names. Snapshot inventory timestamps are unknown when not supplied.
- Site-wide UV/IP/average duration are not added across days. An exact-range
  aggregate can be complete while daily drilldown has gaps; consult aggregation,
  missing dates and field coverage. Paid goals cover all campaigns. CLI is event
  counts, not users or a paired funnel. WeCom customers sum daily counts, not
  unique customers across dates. Neither private source has paid attribution.
- MCP never opens core databases or decrypts private sources. The optional reader
  opens the viewer database `mode=ro`, verifies existing scrypt hashes, returns
  a keyed identity version instead of password material, and decrypts only the
  named fixed-release aggregate. MCP keeps returned private aggregates in request
  memory only. The core reader has no MCP SDK or core application imports.
- MCP's SQLite file is identified with a dedicated `application_id`; foreign
  databases are rejected before writes. MCP cache refuses nonempty unmarked
  directories. Core-side report reads are allowlisted and reject path escapes.
- Observation policies never generate mutations. Decision rules may be scheduled
  to generate unapproved pause drafts only, with explicit IDs, complete data,
  cooldown, expiry and current permission checks. Thresholds come from the caller.
  Restricted automatic rules have a separate owner-only web approval and scope; both execution switches default off. External email/chat delivery is not configured.

## Validation and production boundary

Run the independent suites and worker demonstration:

```sh
services/mcp/.venv/bin/python -m unittest discover -s services/mcp/tests -v
services/core-reader/.venv/bin/python -m unittest discover -s services/core-reader/tests -v
services/mcp/.venv/bin/python -m baidu_ads_mcp.worker --simulate
```

The final local run passed 193 MCP tests and eight standalone reader tests.
The suites cover real SDK stdio and local HTTP OAuth/PKCE/private-tool paths,
identity and token revocation, CSRF/XSS, exports/subscriptions, policy simulation,
proposal → approval → queue → simulated provider → reconciliation, wire contracts,
unknown outcomes and compensation, decimal/coverage semantics and cache imports.
The SDK's lifespan annotation warning is non-failing with the pinned dependencies.

`services/mcp/scripts/verify_capture.py` performs real SDK tool calls against an
explicit authenticated browser capture and saved independent Baidu report API
responses. The 2026-11-17 run passed **69/69** checks across three accounts and
12 advertising report families, website exact ranges and paid goals. Account and
keyword impressions differ upstream; the summary now exposes both sources and
the difference. Private CLI/WeCom calculations use captured values and an explicitly
synthetic identity bridge: this is not independent private-upstream verification.
No captured reports, credentials or current counts belong in Git; inputs/results
remain under ignored `work/mcp-verification/`.

`services/mcp/scripts/verify_isolation.py` exercises local process termination,
reader outages/timeouts, corrupt control/cache data and incompatible/incomplete
source versions. Synthetic core files remain unchanged, and static checks verify
no core imports, requirements or tasks depend on MCP. This does not prove actual
host-level isolation, core availability or production Windows ACL enforcement.

Package independently with `python services/package_services.py --output
work/mcp-artifacts`. The two allowlisted ZIPs include source, locked dependencies,
Windows DPAPI launchers and checksums; no core code, data, credentials or local
state. `.github/workflows/mcp.yml` defines independent Linux/Windows tests and
artifact builds, with no dependency from core workflows. The 2026-11-17 acceptance record covers local runs; remote Linux/Windows CI
results are tracked separately in GitHub Actions. See [deployment notes](DEPLOYMENT.md)
and the [acceptance status](../../docs/MCP_STATUS.md).

**Production acceptance remains separate:** independent host/private ingress and
OS limits; actual Windows DPAPI/service identity; two real Agent clients' OAuth
flows; a complete core collection cycle with fault injection and P95 comparison;
shared-provider quota contention; and explicitly selected per-operation live write
acceptance. No production MCP host, core adapter, advertising credentials or
scheduled advertising execution were installed or enabled here.

Restricted automatic execution is implemented for fixed pause/resume, campaign
budget and manual-bid templates, with per-action/daily/total bounds, independently
approved rule versions and unknown-result stops. The supplied config grants no
`policies:automate` scope and enables neither execution switch. See
[automatic-rule boundaries](AUTOMATIC_RULES.md).

External messaging, CRM/ROI and core-collection triggering were defined as separate
integrations in the plan; they have no configured providers or core command contract.
Unsupported provider contracts (including safe parent cascade deletion and lost
creation provenance) remain explicitly blocked, as detailed in
[execution contracts](EXECUTION.md). This is not blanket production acceptance.
