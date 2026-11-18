# Independent MCP deployment and rollback

This guide describes deployment and rollback. Actual production evidence is
recorded in [the 2026-11-18 deployment record](PRODUCTION_2026-11-18.md). Do not share the MCP process, writable storage, dependency environment or
startup task with the core dashboard/collector. On 2026-11-18 Ken explicitly approved using the existing ECS host. This shares
host outage and resource risk; it does not meet the original separate-host failure
isolation boundary. Independent environments, processes, stores and rollback
remain required. Set `same_host_reader: true` only for the reader at
`http://127.0.0.1:8798`; keep `loopback_development: false` and the public base URL
on HTTPS. The explicit option does not permit remote plaintext reader URLs.
Production activation and actual acceptance results must be recorded separately.

## Artifacts and environment

Build from the checkout with:

```sh
python services/package_services.py --output work/mcp-artifacts
```

Verify `manifest.json` against each ZIP, then the enclosed `SHA256SUMS.json` against
the extracted files. Keep MCP and reader in separate versioned directories. Each
has its own `pyproject.toml` and `uv.lock`; run `uv sync --frozen` in that directory.
Do not install either dependency set into the dashboard or collector environment.

The root `mcp.yml` workflow tests Linux and Windows independently and builds these
two artifacts. It does not deploy or start services, and the core workflows do not
depend on it. Local success does not mean the remote Windows CI run has happened.

## Core-side reader

Use an explicit, restricted service identity with read access only to the fixed
report release paths and dashboard identity database. The existing core service
does not call this reader. Configure Windows CPU/memory limits, private ingress
rate limits and ACL denial of writes to core reports, databases and task files.

The reader starts only on loopback. A separately configured private authenticated
TLS ingress connects it to the MCP host; do not publish the loopback service on a
public route or disable certificate verification. Reader service credential and
identity-version key are independent random values. Existing private snapshot keys
and owner verifier stay on the core host.

Prepare a private DPAPI XML dictionary under the chosen service identity. The
allowlisted variables are in the [reader README](../core-reader/README.md). Secret
values must be Windows `SecureString`; do not paste them into command history or
Git. The launcher supports existing credentials and does not reset any password:

```powershell
.\run_reader.ps1 -SecretConfig 'D:\Private\McpReader\reader.xml'
```

Paths are explicit examples. Use the actual restricted directories for that host.
The launcher is source-reviewed here; native DPAPI and ACL behavior need validation
under the intended Windows service identity before production use.

## Windows process limits

`services/windows_job.py` is included at each artifact root. Launch the service
through this standard-library supervisor, with its PowerShell launcher as the
child command. It applies and reads back a Windows Job Object CPU hard cap,
combined job memory limit and active-process limit before starting any child.
Failure to apply any limit stops startup. Closing the supervisor kills its child
processes. Use a dedicated non-administrator account with batch-logon rights;
deny interactive and remote-interactive logon. Keep launchers and source read-only
for that account and keep each service's state/config directory private.

For example, from an extracted artifact directory:

```powershell
python ./windows_job.py --memory-mb 768 --cpu-percent 20 -- powershell.exe -NoProfile -NonInteractive -File ./run_mcp.ps1 -ReleaseCopy '<verified-copy>' -HttpConfig '<private-config>' -SecretConfig '<DPAPI-config>'
```

The reader can use 512 MiB / 15% CPU; the deployed MCP web service uses 768 MiB /
20% CPU. Both allow at most six processes including the supervisor. These limits
reduce contention; they do not provide host-outage or shared-ingress isolation.
Native Windows tests attempt a 512 MiB allocation in a 128 MiB job and verify that
child exit status propagates. Run these tests on the target Windows version too.

## MCP host

Create a dedicated writable state/cache directory. Configure a real HTTPS base URL,
exact Agent client redirects, grants and the private reader URL using
`http-config.example.json`; disable `loopback_development`. Set `dynamic_client_registration: true` to advertise standard OAuth client
registration for heterogeneous Agent clients. Static registrations remain supported.
Dynamic registration accepts exact HTTPS or loopback redirects, uses S256 PKCE,
and does not grant any data access until a dashboard identity consents to its
authorized scopes. Defaults request only analytics/settings reads. Registrations
are bounded to 30 per minute and 1,000 total; apply ingress rate/body limits too.
Client metadata/secrets are encrypted in the MCP-owned database using a
domain-separated key derived from the private bridge credential. Rotating that
credential invalidates dynamic registrations; clients must register and authorize
again. Do not treat a client-supplied role/account as authorization. Actual
Codex/ChatGPT/Claude compatibility must be verified separately; standard protocol
support is not evidence that every client has been tested.

Keep a separate DPAPI XML dictionary containing `MCP_BRIDGE_SERVICE_TOKEN` as a
`SecureString`. Run from the extracted service directory:

```powershell
.\run_mcp.ps1 -ReleaseCopy 'D:\Private\Mcp\cache\releases\<revision>' -HttpConfig 'D:\Private\Mcp\http.json' -SecretConfig 'D:\Private\Mcp\secrets.xml'
```

The initial release must be a complete, verified, explicitly selected copy with
all three report HTML files. Do not use raw SQLite/CSV inputs. The HTTP service
binds loopback; its own HTTPS reverse proxy must preserve the intended Host/Origin
and resource URL. Export/download and management pages stay authenticated.

`auto_refresh` is opt-in. It requires a configured subject's current authorization
and bridge identity on every cycle, polls no faster than five minutes and backs
off on failure. Stale snapshots are rejected; refresh never starts core collection.
Background jobs/exports/subscriptions are owned by MCP, not by Windows core tasks.

## Advertising execution

No production write credential or enabled capability is shipped. The service and
worker switches default off. `--simulate` exercises a temporary synthetic provider:

```sh
python -m baidu_ads_mcp.worker --simulate
```

Only a separately reviewed per-account capability may identify actual provider
version, field semantics, verified enum values, preconditions, expiry, monetary
limits and evidence. Source SDK compatibility is insufficient to enable an account.
Every enabled path still requires fresh author/approver identity, fixed approval,
idempotency and current provider state. The worker is a separate bounded process:

```sh
python -m baidu_ads_mcp.worker --config /private/mcp/http.json --limit 10
```

Deletion is independently permissioned and irreversible. Unknown submissions keep
locks and require reconciliation; never repair them by editing the SQLite database
or resubmitting. An owner-entered created-object ID requires authoritative provider
creation evidence before acceptance. New parent/child objects use reconciled IDs
in subsequent separately reviewed plans.

## Acceptance and rollback

Before activation, run the complete local suites, `verify_isolation.py`, real Agent
OAuth/refresh/revocation checks, and fixed-revision real-data QA. Validate Windows
DPAPI, ACLs, separate-host failure, resource exhaustion, provider quota contention
and a complete daily core collection cycle. Measure core login/report/collection
availability and P95 under the same load; source-level separation is not proof of
host fault isolation. Live write acceptance requires individually selected test
objects and exact approved changes, not blanket activation of all capabilities.

To roll back, disable new execution first and preserve in-flight reconciliation and
audit state. Stop the independent MCP service/worker, restore the previous MCP
artifact, and retain its compatible control database. Never silently downgrade an
incompatible state schema. Stop/remove the reader and revoke its ingress/credential
only after MCP is disabled. Core code, databases, login task, collector task and
publication pointer require no rollback and must not be changed for MCP removal.

## Operation history

Authenticated `/activity` shows MCP tool calls, login/consent outcomes and signed-in
management POST actions. Owners see all users; other users see only their own
records. The page uses 50-row cursor pagination and Beijing timestamps. Metadata
contains authenticated actor, fixed operation name, allowlisted account/date or
plan/job identifier, outcome and duration. It never stores passwords, OAuth
secrets, submitted free text, complete parameters, query result counts or IPs.
Failed sign-ins use the `anonymous` actor rather than an unverified account name.

Records live in the MCP-owned control database, bounded to the latest 90 days and
100,000 entries; maintenance runs on new log writes. Started-but-unfinished calls
remain marked as such after a crash; they must not be read as successful. A log
write failure fails the MCP operation rather than silently losing tracking. Core
services do not import or wait for this optional log. Existing versioned change
plan audit events remain separate and unchanged. This is operation tracking, not
a tamper-proof external audit archive; administrators still control the local disk.
Browser GETs, requests rejected before MCP authentication, and background execution
steps are not a complete web access log. Durable job state and existing plan events
remain the evidence for actual background completion or advertising execution.
