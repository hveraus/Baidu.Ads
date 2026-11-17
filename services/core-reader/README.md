# Optional core-side read/identity adapter

Development implementation, **not deployed or production approved**. This process
is removable and independent: core login/collection never calls or imports it.
It has its own environment and lock file; it does not depend on the MCP SDK.

From this directory:

```sh
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python reader.py
```

Startup requires private configuration in the process environment:

| Variable | Source / meaning |
|---|---|
| `MCP_READER_STORAGE` | Existing core immutable release storage, OS read-only permission |
| `MCP_READER_USERS_DB` | Existing dashboard viewer database, opened in SQLite `mode=ro` |
| `MCP_READER_OWNER_HASH` | Existing owner scrypt verifier, supplied privately by deployment bootstrap |
| `MCP_READER_VERSION_KEY` | Independent random key, at least 32 bytes, hex; keyed identity-version derivation |
| `MCP_READER_SERVICE_TOKEN` | Independent random service credential, at least 43 characters; matches MCP bridge credential |
| `MCP_READER_CLI_KEY` | Optional existing CLI AES-256 key, hex, retained on core host only |
| `MCP_READER_WECOM_KEY` | Optional existing WeCom AES-256 key, hex, retained on core host only |

No secrets are read from Git or emitted. `run_reader.ps1 -SecretConfig <private DPAPI XML>` maps only the allowlisted
settings into the child process and restores the environment on exit. It requires
SecureString values for secrets and explicit absolute data paths. The launcher is
implemented but has not been installed or validated on production Windows. Do not put these values in shell history,
source code, reports or logs. Owner verifier changes require the same restart
semantics as the core owner's startup configuration; viewer resets are read live.

The reader binds only `127.0.0.1:8798`, accepts service-authenticated POSTs, and
supports only `authenticate`, `identity`, `current`, `report`, `private`. All data
reads also require current subject/version. Only the three report files and two
private encrypted aggregate paths are exposed. No filesystem path parameter,
SQL, shell, collector commands or writes are accepted. Unknown operations reject.
Identity versions derive from the canonical subject and current password verifier;
no verifier is returned. Private payloads contain only the approved aggregate fields.

Requests cap at 8 KiB, socket reads at five seconds, active handler threads at four,
scrypt at two concurrent calls/five attempts per minute. The report file limit is
32 MiB, private files 16 KiB. These application limits are not a substitute for OS
memory/CPU limits: a scrypt call can use substantial memory. Production private TLS
ingress, bounded service identity, ACLs, Windows runtime, OS limits and fault tests
remain necessary. Do not publish the loopback listener directly to the internet.

Tests use a disposable synthetic viewer database and locally generated encrypted
snapshots. No actual dashboard password, private counts or production keys are used.
