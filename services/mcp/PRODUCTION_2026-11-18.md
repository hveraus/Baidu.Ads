# Production MCP deployment — 2026-11-18

Status: HTTPS service installed; authenticated end-to-end acceptance is pending.
This record contains operational evidence only. No keys, passwords, tokens, raw
advertising files or private CLI/WeCom counts belong here.

## Installed and observed

- Deployment uses the existing Aliyun Windows Server 2016 host, as explicitly
  requested by Ken. Host and Nginx remain shared with the dashboard. This is not
  the originally proposed separate-host failure boundary.
- Public endpoint: `https://mcp.ads.ssw.com.cn/mcp`. DNS resolves to the existing
  host. OAuth discovery over certificate-validated public HTTPS returned 200.
  Dynamic client registration was exercised with an exact loopback callback and
  S256 PKCE; the browser reached the login/consent page.
- Runtime source baseline: `baf873c`. MCP and core-reader have independent Python
  3.14 virtual environments. No core environment dependencies were changed.
- Dedicated non-administrator accounts `BaiduMcpWeb` and `BaiduMcpReader` have
  batch-logon rights and deny interactive/remote-interactive logon. Their startup
  tasks are `baidu-ads-mcp-web` and `baidu-ads-mcp-reader`. Both use Windows
  user-scoped DPAPI configuration verified under the actual task identity.
- MCP listens only at `127.0.0.1:8797`, reader at `127.0.0.1:8798`. The reader is
  not exposed through public Nginx. MCP state lives separately in
  `C:/ProgramData/BaiduAdsMcp/web`; reader configuration lives in the sibling
  `reader` directory. Source and supervisor launchers are read-only to the task
  identities. Administrators and SYSTEM retain maintenance access.
- Reader job limit: 512 MiB / 15% CPU. Web job limit: 768 MiB / 20% CPU.
  Both are limited to six active processes, including the supervisor.
- Let's Encrypt certificate was issued for this host only, expiring 2027-02-16.
  Certify auto-renewal is enabled and its PEM export task succeeded. The separate
  `baidu-ads-mcp-certificate-reload` SYSTEM task checks the certificate fingerprint
  every 15 minutes, validates Nginx configuration and reloads only on change.
  Its initial run returned 0. Certificate/key files are restricted to SYSTEM and
  Administrators. Future renewal itself has not yet occurred.
- Nginx preserves the MCP host and forwards HTTPS scheme, streams responses
  without buffering, caps request bodies at 1 MiB and limits requests to
  10/second per source IP with burst 20. Access logging is disabled for this host.
  HTTP challenge handling is preserved; other HTTP requests redirect to HTTPS.
- Production advertising execution and automatic mutations remain disabled.
  No advertising write credentials were installed.

## Verification completed

- 201 MCP tests and 8 reader tests passed locally. The previous native Windows
  run also passed those suites. Both added Windows-only supervisor tests passed
  on the production Windows Server 2016 host (memory enforcement and exit status).
  GitHub run [44229307101](https://github.com/hveraus/Baidu.Ads/actions/runs/44229307101)
  passed all four Linux/Windows service jobs and artifact packaging at `f0619bc`.
- Real SDK stdio handshake listed 46 tools. Three-account summary results matched
  independent sums of the selected immutable production HTML reports. This did
  not establish public HTTP OAuth/private-data acceptance.
- Initial MCP-owned cache was explicitly initialized with its ownership marker,
  release pointer, import time and verified hashes; service startup then succeeded.
- Under both production task identities, actual file opens confirmed denial of
  writes to the core publication pointer and identity database, and denial of
  reads to the core login secret configuration. Only the reader could read the
  core pointer and identity database. All ten checks passed.
- A real 512 MiB allocation inside a 128 MiB Windows job failed with MemoryError,
  as required. The original dashboard health endpoint still returned 200.
- Core dashboard task was running. The collector's 2026-11-18 09:00 execution
  returned 0; next run remained 2026-11-19 09:00. This collection occurred before
  MCP activation, so a complete post-activation daily cycle remains unobserved.
- Stopping the MCP web task removed its listening socket while core health
  remained 200. Both optional tasks restarted with new process IDs; MCP OAuth
  discovery returned 200 again. Independently stopping the reader removed port
  8798 while core health remained 200; the reader then recovered its listener.
- Public anonymous `/mcp` returned 401 with a valid protected-resource discovery
  URL; that discovery returned 200. An untrusted Origin returned 403. All three
  existing report entry URLs returned 200 (this alone is not authenticated QA).
- Temporary machine-scoped bootstrap blobs were consumed and removed after
  user-scoped DPAPI configuration succeeded; no bootstrap blob remained. The
  two one-time bootstrap tasks were disabled after verification.

## Pending acceptance and access decisions

- A real browser sign-in and consent, token exchange, HTTP MCP query, private
  CLI/WeCom query and reconciliation against authenticated production dashboards.
- Authenticated automatic cache refresh is not yet enabled. The initial cache
  has a one-hour freshness limit; stale data is rejected rather than presented
  as current. Startup alone does not establish a refresh identity.
- `kenshi` is the only configured MCP grant. Colleague access expansion awaits an
  explicit decision; existing dashboard access has not been changed. New users
  are not silently granted MCP permissions.
- Client-specific Codex/ChatGPT/Claude compatibility,
  authenticated refresh/revocation, post-activation collection and load/P95 checks.
  Do not claim full host/resource/upstream fault isolation from current checks.

## Removal

Stop and disable the independent MCP web and reader tasks; preserve their state
for audit or rollback. Remove only the `baidu-ads-mcp/site.conf` include from the
Nginx configuration, validate, then reload through the dedicated SYSTEM task.
Remove the MCP certificate reload task and Certify managed certificate only when
retiring the endpoint. Core dashboard/collector source, tasks and publication
pointer do not need to be changed. Remove dedicated reader ACL grants/accounts
only after both optional services are stopped and no pending job needs evidence.
