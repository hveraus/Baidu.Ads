# Independent MCP deployment and rollback

These are prepared deployment instructions, not a record that production was
changed. Do not share the MCP process, writable storage, dependency environment or
startup task with the core dashboard/collector. A separate host and private reader
ingress remain the planned production boundary.

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

## MCP host

Create a dedicated writable state/cache directory. Configure a real HTTPS base URL,
exact Agent client redirects, grants and the private reader URL using
`http-config.example.json`; disable `loopback_development`. Client registration is
static. Do not treat a client-supplied role/account as authorization.

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
