---
title: SSE MCP reverse-proxy for Hermes profiles
status: approved
target: ~/.hermes/profiles/hermes-vc/bin/mcp_reverse_proxy.py
created: 2026-07-05
session_id: 20260705_105103_e1dfa4
supersedes_session: 20260705_105103_e1dfa4
---

## Problem

The vc-client Hermes profile wires a single MCP server entry:

```yaml
mcp_servers:
  hermes:
    url: http://localhost:9119/sse
    transport: sse
```

The intent was to front seven heavy MCP servers (code-review-graph,
DocumentDB, Astro, DeepWiki, context7, pencil, shadcn) — declared once
in the hermes-vc profile — through one shared SSE endpoint so vc-client
does not have to spawn seven subprocesses of its own.

But **port 9119 is the Hermes dashboard's uvicorn server**, not an MCP
gateway. There is no SSE MCP reverse-proxy in the Hermes codebase.

Diagnostic evidence (session `20260705_105103_e1dfa4`):

```bash
$ ps -p 6294 -o cmd
hermes_cli.main -p default dashboard --port 9119 --host 127.0.0.1 \
    --open-profile hermes-vc --no-open

$ curl -sS -o /dev/null -w "%{http_code} %{content_type}\n" \
    --max-time 3 http://localhost:9119/sse
200 text/html
```

The vc-client agent logs the failure at every session start:

```
~/.hermes/profiles/vc-client/logs/agent.log
2026-07-05 10:48:08,707 WARNING tools.mcp_tool: MCP server 'hermes' initial connection failed (attempt 1/3), retrying in 1s
httpx_sse._exceptions.SSEError: Expected response header Content-Type to contain 'text/event-stream', got 'text/html'
2026-07-05 10:48:15,766 WARNING tools.mcp_tool: MCP server 'hermes' failed initial connection after 3 attempts, giving up
2026-07-05 10:48:15,767 INFO  tools.mcp_tool: MCP: registered 0 tool(s) from 0 server(s) (1 failed)
```

Why "no built-in proxy":

```bash
$ grep -rn --include='*.py' 'SseServerTransport|StreamableHTTPServerTransport' \
    /home/raphael/.hermes/hermes-agent \
    | grep -v '/venv/' | grep -v '/tests/'
# (zero hits in non-vendored, non-test code)
```

The `hermes mcp serve` subcommand only exposes Hermes-as-MCP-server
(outbound direction); it takes `-v` and `--accept-hooks` and nothing
else. The `http://127.0.0.1:8000/sse` default at
`hermes_cli/profile_distribution.py:44` is the Graphiti memory MCP
example URL, not a Hermes-managed proxy.

### Why Talaria does not ship an MCP proxy

The reverse-proxy is **not** a Talaria deliverable. Talaria is
read-only against the Hermes runtime (per `src/talaria/AGENTS.md`),
and writing a port-binding long-running process would be a first.
The owning folder is `~/.hermes/profiles/hermes-vc/bin/` — outside
this repo — so the proxy is a **profile artefact**, not a Talaria
artefact. The proxy script ships under the hermes-vc profile's
`distribution.yaml` if and only if that profile is published.

What Talaria **already does** for this integration:

* `talaria config sync --add-mcp-serve` (default-on with
  `--mcp-serve-port 9119 --mcp-serve-name hermes`) writes the
  `mcp_servers.hermes` block into a target profile's `config.yaml`,
  pointing at `http://localhost:<port>/sse`. See
  `src/talaria/sync/mcp_serve.py` and the
  `DEFAULT_MCP_SERVE_PORT` constant in
  `src/talaria/sync/paths.py`. The proxy just needs to serve
  that URL.
* `talaria hermes serve-stop` (`src/talaria/hermos/serve_stop.py`)
  already implements the exact PID-by-port discovery pattern this
  proxy needs (`psutil.net_connections("inet")` → PID, the same
  way `hermes serve --stop` is being fixed). Its lifecycle
  (graceful SIGTERM → poll → SIGKILL within `GRACEFUL_TIMEOUT_SECONDS`)
  is the prototype for the proxy's `start` / `stop` / `doctor` /
  `status` CLI surface — described in Implementation, not imported.
* The bash-completion system introspects `build_parser()` at
  invocation time (see `src/talaria/cli/AGENTS.md` §completion
  contract). Any operator-side `mcp-proxy` command added under
  Talaria in a future revision would have to honour the same
  silent-by-default discipline — but that is **not** the proxy
  this spec describes.

### Rejected: importing `jcnh74-hermes-bridge`

The `hermes-bridge` project at
`~/src/rb/jcnh74-hermes-bridge` was considered as code reuse:

* It is a REST+SSE gateway from mobile clients to Hermes agents,
  binding default port 8765 (not 9119 or 8000).
* It does **not** import `mcp`; `grep -rn 'mcp\|stdio_client\|sse_client'`
  returns zero matches. It speaks `fastapi` + `sse-starlette` to
  humans, and `import run_agent` directly from Hermes. The
  architectural style (subprocess-per-upstream vs in-process
  import) is the inverse of what an MCP fan-out needs.
* `server.py` (42KB) and `agent_proxy.py` (27KB) are tied to
  in-process Hermes invocation and have no analogue in an MCP
  proxy. Forcing overlap would create a maintenance fork the
  operator does not want.

What does get reused from `hermes-bridge` is **lifecycle
ergonomics** — the four patterns enumerated in
Implementation §Lifecycle. Those are conceptual idioms
(PID files keyed by port, doctor with locate→import→resolve,
parent-detaches-with-fork+setsid, signal-poll stop), not
importable code: implementing them in the proxy is ~30 lines
of glue and stays inside `hermes_bridge`-style operational
discipline the operator already runs in other services.

## Goal

Add a single SSE MCP reverse-proxy process that:

1. Reads one profile's `mcp_servers:` block at startup.
2. Spawns each non-`url:` server as a stdio MCP subprocess;
   connects to each `url:` server over its declared transport
   (sse or streamable_http).
3. Re-exposes the unioned `tools/list`, `tools/call`,
   `resources/*` on a single HTTP+SSE endpoint at
   `http://<host>:<port>/sse` with POST `/messages`.
4. Is launched automatically alongside the Hermes dashboard so
   the URL configured by `talaria config sync --add-mcp-serve`
   is permanently reachable.

End state: vc-client's existing `mcp_servers.hermes` config
starts working, the SSE error in `agent.log` disappears, and
`mcp__code-review-graph__*`, `mcp__DocumentDB__*`,
`mcp__context7__*`, etc. tools become visible in vc-client
sessions without spawning seven subprocesses per harness.

The proxy is **port-9119-adjacent, not 9119 itself**: see
Design §Port allocation for the decision.

## Non-goals

- Replacing the underlying Hermes `tools/mcp_tool.py` MCP
  discovery loop. The proxy speaks standard MCP over SSE; Hermes
  talks to it as it would any SSE MCP server.
- Auth, OAuth, or rate-limiting. The proxy binds to
  `127.0.0.1` only.
- Dynamic reload of `mcp_servers:`. Config is read once at
  startup. Restart the proxy to pick up changes.
- Proxying the `hermes` mcp_servers entry itself if it ever
  appears (skip self-references).
- Reworking the dashboard. The proxy is a separate process
  bound on its own port; the dashboard keeps 9119.
- Becoming a Talaria CLI subcommand. The proxy is a
  profile-owned daemon; Talaria ships the `sync
  --add-mcp-serve` writer that produces the matching
  vc-client config but does not implement the proxy
  process.
- Talking to the operator's `talaria hermes serve-stop` tool
  for shutdown. The proxy has its own PID file keyed by port
  (see Implementation §Lifecycle) so multiple proxies on
  different ports do not clobber each other.

## Constraints

- **Bind to `127.0.0.1` only.** Public exposure is a security
  bug (code-review-graph has no auth; DocumentDB inherits
  the local MongoDB connection). Use `127.0.0.1` as the
  default `--host`; reject `0.0.0.0` / public IPs at startup
  with a clear error.
- **No new pip dependencies in the hermes-vc venv.** Use only
  `mcp` (already in the Hermes venv) and stdlib (`asyncio`,
  `logging`, `signal`, `argparse`, `pathlib`, `subprocess`,
  `json`, `threading`). `yaml` is fine to import — it is
  already a hard dep of Talaria (`pyproject.toml`) and of
  Hermes, so it is in the runtime path.
- **Profile resolution.** Resolve the hermes-vc profile path
  the same way Talaria does (`src/talaria/paths.py`):
  `--profile <name>` flag wins, then `HERMES_PROFILE` env,
  then `~/.hermes/active_profile`, then `"default"`. The
  `--config` flag may also point at any absolute path to a
  `config.yaml` (mirrors `talaria.sync.paths.resolve_profile`).
- **stderr-only logging.** stderr is the operator-visible
  log channel; stdout is reserved for data. Format:
  `%(asctime)s [%(levelname)s] %(name)s: %(message)s`,
  matching Hermes' own convention.
- **Graceful shutdown on SIGTERM/SIGINT.** Drain in-flight
  tool calls (max `GRACEFUL_TIMEOUT_SECONDS = 5.0`,
  matching `talaria.hermos.serve_stop`), close each
  upstream session, exit 0. SIGKILL must be the last
  resort, never the default.
- **Per-upstream error isolation.** If one upstream server
  fails `initialize`, log it and continue with the rest. A
  single broken server must not prevent the proxy from
  registering the other six.
- **Do not modify `tools/mcp_tool.py`.** The proxy is a
  separate service the existing MCP client code talks to via
  standard SSE.
- **Mirror the hermes-vc config schema.** The `mcp_servers:`
  shape used by `tools/mcp_tool.py` (command/args/env for
  stdio, url for HTTP, transport: sse) is the input format.
  Reject unknown transports at startup with a clear message
  rather than failing silently at first tool call.
- **Tool-name prefixing (routing key).** Each tool exposed
  by the unioned `tools/list` is prefixed with its
  originating server key as `<server-key>__<tool-name>`.
  The proxy's `tools/call` handler strips the prefix and
  forwards to the matching upstream. This makes routing
  deterministic and removes tool-name collisions without
  requiring a `priority:` field in the config schema.
- **Skip self-references by name.** Any `mcp_servers.hermes`
  entry whose URL/transport points at the proxy itself
  (loopback on the configured `--host:--port`) is dropped
  during config load. This prevents the proxy from
  proxying itself if the operator copies the vc-client
  block into hermes-vc.
- **Match Hermes' Hermes-as-server fingerprint when
  observability needs the name.** The proxy's JSON-RPC
  `initialize` response advertises
  `serverInfo.name="hermes-vc-mcp-proxy"` and protocol
  version `2024-11-05`. This must stay stable across
  releases so `tools/mcp_tool.py`'s debug logs keep
  identifying the proxy.

## Design

### Port allocation

Three options existed; the implementer no longer chooses.
**Option P2 is fixed** by this revision:

- **P2 (chosen).** Keep dashboard on 9119 (where
  `serve-stop` finds it and `talaria config sync
  --mcp-serve-port` points). Move the proxy to **port 8000**
  to match Hermes' documented Graphiti MCP example URL at
  `hermes_cli/profile_distribution.py:44`. **vc-client's
  `mcp_servers.hermes.url` becomes
  `http://localhost:8000/sse`.** This is the cheapest
  change and aligns the proxy's port with the one
  example Hermes docs already cite for MCP SSE.

Implementation steps that fall out of P2:

- `talaria config sync --mcp-serve-port` and
  `--mcp-serve-host` already let the operator set the
  port per call (see
  `src/talaria/cli/__init__.py:1198`).
  `talaria config sync --add-mcp-serve --mcp-serve-port 8000`
  produces the matching vc-client config.
- The proxy defaults to `--port 8000 --host 127.0.0.1`,
  hardcoded constant. Operators can override per-launch
  via a CLI flag; the PID file is keyed by port so
  multiple proxies on different ports stay separate
  (`~/.hermes/logs/mcp-proxy-<port>.pid`).
- `talaria hermes serve-stop --port 8000` becomes the
  canonical way to stop the proxy without resorting
  to `pkill -f`. The existing `serve_stop` module
  already handles arbitrary ports via `--port`, so no
  change to Talaria is required.

#### Open questions resolved by P2

- "Open Question 1" in the prior revision of this spec
  asked P1/P2/P3. **P2 selected; P1 rejected (no need to
  change dashboard landing pages); P3 rejected (the "wait,
  race-prone" option the operator flagged as
  over-engineering).**
- "Open Question 5" (distribution ownership) is unchanged:
  if `~/.hermes/profiles/hermes-vc/distribution.yaml` exists,
  add the proxy script to its `distribution_owned:` list.

### What was considered

1. **`hermes mcp serve --port 8000`** — rejected; the
   subcommand has no `--port` flag and goes the opposite
   direction.
2. **Patch the dashboard to also serve MCP** — rejected;
   the dashboard is a FastAPI app for the React SPA,
   and layering MCP routes onto it would couple
   unrelated lifecycles.
3. **Spawn the seven servers directly in vc-client** —
   rejected; the original intent was specifically to
   *avoid* the seven copies per harness, and this
   would also double-spawn when both hermes-vc and
   vc-client are running.
4. **A small dedicated reverse-proxy script** — chosen.
   Reads the upstream config once, mounts one FastMCP
   SSE server on the chosen port, fans every incoming
   request out to the matching upstream by tool-name
   prefix (`<server-key>__<tool-name>`).
5. **Reuse code from `hermes-bridge`**
   (`~/src/rb/jcnh74-hermes-bridge/`) — rejected for
   the MCP core (zero overlap), accepted for lifecycle
   ergonomics (four patterns copied as idioms, not as
   imports).

### Architecture

```
                                    ┌─ code-review-graph (uvx stdio)
                                    ├─ DocumentDB       (npx stdio)
                                    ├─ context7         (npx stdio)
                                    ├─ pencil           (windows .exe stdio)
                                    ├─ shadcn           (npx stdio)
                                    ├─ Astro            (https streamable)
                                    └─ DeepWiki         (https streamable)
                                          │
                                          │ one stdio / streamable_http client per upstream
                                          ▼
                       ┌──────────────────────────────────────┐
                       │  mcp_reverse_proxy.py                │
                       │  ─ merged tools/list (prefix-keyed)  │
                       │  ─ routes tools/call by prefix       │
                       │  ─ single FastMCP SSE app on :8000   │
                       │  ─ PID-by-port lifecycle              │
                       └──────────────────────────────────────┘
                                          │
                                          │ SSE :8000/sse + POST :8000/messages
                                          ▼
                       ┌──────────────────────────────────────┐
                       │  Hermes MCP client                   │
                       │  (tools/mcp_tool.py — already exists)│
                       └──────────────────────────────────────┘
```

The merged `tools/list` names each tool
`<server-key>__<tool-name>`. The Hermes client prefixes by
`mcp__<server-key>__<tool-name>` on its side per the
existing convention, so the operator-visible tool name
becomes `mcp__code-review-graph__code-review-graph__list_repositories`
if the prefix key is also `code-review-graph`.

**Mitigation:** the proxy reads the existing
`mcp_servers:<key>` keys and uses them as the merge
prefix. The Hermes `tools/mcp_tool.py` convention adds
its own `mcp__<server>__` on top, so the final
operator-visible name is
`mcp__hermes__<key>__<tool-name>`. The vc-client profile
already names the singleton entry `mcp_servers.hermes`,
which inverts the conflict: the final name becomes
`mcp__hermes__DocumentDB__list_collections`. The proxy
can adopt one of two prefixing schemes to keep the
final shape tidy:

- **Scheme A (recommended).** Drop the server-key
  prefix entirely; advertise upstream tools verbatim
  under their original names. The Hermes client adds
  `mcp__<server>__` based on the entry name `hermes`,
  producing `mcp__hermes__list_collections`. The proxy
  routes by inspecting the request's `server_key`
  (which the client sends in `params.serverKey`) when
  present; otherwise it routes by first match in the
  upstream-tool map.

- **Scheme B (fallback).** Keep the
  `<key>__<tool-name>` prefix, producing
  `mcp__hermes__DocumentDB__list_collections`. The
  implementer chooses A unless the Hermes client
  doesn't propagate `serverKey` (verify via
  `hermes/tools/mcp_tool.py`).

The implementer MUST verify which scheme Hermes uses
before coding the merge; the §Verification step 5
includes a one-time check.

## Implementation

### File to create

`~/.hermes/profiles/hermes-vc/bin/mcp_reverse_proxy.py`
(realistic size: 200–300 lines).

The file is **profile-owned** (under the hermes-vc
profile), so it ships as a profile artefact, not a
Hermes release artefact. Add it to the hermes-vc
profile's distribution-owned paths if applicable.

**Shape, not literal code.** The skeleton's
`FastMCP(...).add_tool(...)` from the prior revision
is pseudocode that won't run as written — `FastMCP`
expects decorator-based tools and does not accept a
`Server` instance as a tool argument. The implementer
picks one of two working patterns:

- **`mcp.server.Server` with `@server.list_tools()`
  / `@server.call_tool()` decorators** wrapped in an
  `asyncio` SSE transport. This is what
  `hermes/tools/mcp_tool.py` itself uses, so the
  proxy and the Hermes client match transports
  exactly.
- **`mcp.server.fastmcp.FastMCP`** with
  `@mcp_server.list_tools()` /
  `@mcp_server.call_tool()` decorators. Higher-level,
  but requires care to mount on a hand-rolled SSE app
  that pins `/sse` + `/messages` rather than FastMCP's
  defaults.

**Pick whichever matches the version of the `mcp`
SDK installed in the Hermes venv.** The skeleton's
`server.add_tool(proxy.list_tools)` is replaced by
decorator registration against a real
`server: Server` or `mcp_server: FastMCP` instance.

### Per-upstream connection

```python
async def connect_upstream(
    name: str, cfg: dict
) -> tuple[str, ClientSession, list[str]]:
    """Spawn one upstream MCP client. Return (name, session, tool_names).

    `name` is the mcp_servers: block key (e.g. 'DocumentDB').
    `cfg` is the raw dict from config.yaml:

        command/args/env  -> StdioServerParameters -> stdio_client
        url + transport:sse -> sse_client
        url + transport:streamable_http -> streamable_http_client
        (anything else -> raise ProxyConfigError at startup)
    """
```

`name` (the mcp_servers: key) is the routing prefix used
in both Scheme A and Scheme B above. Failed
`initialize` logs a warning and skips the upstream;
the remaining upstreams register normally.

### Reverse-proxy core

```python
class ReverseProxy:
    def __init__(self, upstreams: dict[str, ClientSession]):
        self.upstreams = upstreams
        self.routing: dict[tuple[str, str], str] = {}
        # Map (server_key, tool_name) -> server_key for upstream routing.
        # Built by concatenating tools/list results across upstreams.

    async def list_tools(self) -> ListToolsResult:
        # Sum tools/list across upstreams, decorated per the prefix scheme.
        ...

    async def call_tool(
        self, server_key: str, tool_name: str, arguments: dict
    ) -> CallToolResult:
        # server_key is the mcp_servers: key the client sent (Scheme A).
        # tool_name is the original upstream tool name.
        return await self.upstreams[server_key].call_tool(
            tool_name, arguments
        )
```

`server_key` is `None` when the client does not send
it (Hermes' current convention); the proxy routes by
matching `tool_name` against `self.routing` directly.
Routing never depends on tool-name uniqueness — a
collision becomes a `RouteAmbiguous` error returned
to the client.

### Lifecycle (idioms, not imports)

Four patterns, replicated as design idioms in the
proxy script. No code is imported from
`hermes-bridge`; each pattern is described in enough
detail that the implementer can reproduce it in
~30 lines of glue:

- **PID file keyed by port.** Write
  `~/.hermes/logs/mcp-proxy-<port>.pid` on daemon
  startup. `stop` reads the same path, sends SIGTERM,
  polls `os.kill(pid, 0)` in a 0.5s loop up to 5s,
  then SIGKILLs survivors. Mirrors
  `hermes-bridge`'s `cli.py:22-28` and
  `talaria.hermos.serve_stop` (already in the
  operator's runtime for the 9119 dashboard).
- **`doctor` with three checks.** Locate the
  profile's `config.yaml`; verify `mcp` imports
  resolve; non-fatally attempt `connect_upstream`
  for every `mcp_servers:` entry (warnings about
  failures, exit 0).
- **Daemonize via `os.fork() + setsid()`.** Parent
  prints the URL and exits; child `setsid()`s,
  redirects stdout/stderr to a log file, writes the
  PID file, then runs the asyncio loop. Replaces
  the shell `&` wrapper from the prior revision,
  which lost logs and broke SIGTERM forwarding.
- **SIGTERM-poll-then-SIGKILL stop.** Same algorithm
  as `talaria.hermos.serve_stop`, with
  `GRACEFUL_TIMEOUT_SECONDS = 5.0` and
  `POLL_INTERVAL_SECONDS = 0.1`. Constants match
  `serve_stop` exactly so the operator sees one
  stop expectation across both daemons.

### Launch wiring

Adopt L3 above. The proxy's own argv parser is:

```
mcp_reverse_proxy.py [--profile NAME | --config PATH] \
                     [--host 127.0.0.1] [--port 8000] \
                     [--log-level INFO] \
                     <command>
```

`<command>` is one of:

- `start [--foreground]` — daemonize by default,
  foreground if `--foreground`. Preflight (L2)
  runs unless `--skip-checks` is set.
- `stop` — SIGTERM via L1, exit 0 when stopped or
  no PID file, exit 2 on partial failure.
- `status` — print running PID + URL + log path or
  "not running".
- `doctor` — run L2 standalone (no daemon).
- `restart` — `stop`; `start`.
- `version` — print the proxy version constant and
  exit.

Default `command` is `start --foreground` if the
operator runs no subcommand (preserves the
single-flag ergonomics from the prior revision).

### Defaults

```python
PROFILE_DEFAULT = "hermes-vc"
HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8000
GRACEFUL_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1
PID_PATH_TEMPLATE = "~/.hermes/logs/mcp-proxy-{port}.pid"
LOG_PATH_TEMPLATE = "~/.hermes/logs/mcp-proxy-{port}.log"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "hermes-vc-mcp-proxy"
```

The `--host` value is rejected at startup when it is
not loopback (`127.0.0.1`, `::1`, `localhost`) — a
clear error referencing this spec's bind-to-loopback
constraint, not a generic argparse failure.

### vc-client config

The operator produces this with one Talaria command:

```bash
talaria config sync vc hermes-vc \
    --add-mcp-serve --mcp-serve-port 8000 --mcp-serve-name hermes
```

Result in `~/.hermes/profiles/vc-client/config.yaml`:

```yaml
mcp_servers:
  hermes:
    url: http://localhost:8000/sse
    transport: sse
```

The `mcp_discovery_timeout` in vc-client (default
1.5s) may need to be raised to ~5s to give the
proxy + seven upstreams time to `initialize`. Bump
via `mcp_discovery_timeout: 5` in vc-client's
`config.yaml`.

## Verification

The implementer must run each step and capture
output.

```bash
# 1. The proxy starts cleanly and binds 8000.
python3 ~/.hermes/profiles/hermes-vc/bin/mcp_reverse_proxy.py \
    --profile hermes-vc --port 8000 --log-level INFO start --foreground &
PROXY_PID=$!
sleep 3
ss -ltn 'sport = :8000'
# Expect: LISTEN ... 127.0.0.1:8000 ... users:(("python3",pid=<PROXY_PID>,...))

# 2. /sse returns text/event-stream (NOT text/html).
curl -sS -o /dev/null -w "sse=%{http_code} ct=%{content_type}\n" \
    --max-time 3 http://localhost:8000/sse
# Expect: sse=200 ct=text/event-stream

# 3. /messages accepts POST with a JSON-RPC initialize.
curl -sS -X POST -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"diag","version":"0"}}}' \
    http://localhost:8000/messages
# Expect: 200 + JSON-RPC initialize response with
# serverInfo.name="hermes-vc-mcp-proxy"
# and capabilities (no error)

# 4. vc-client discovers the proxy and registers all upstream tools.
# Run the sync command above first, then restart vc-client, then check:
tail -50 ~/.hermes/profiles/vc-client/logs/agent.log | grep -E "MCP:"
# Expect: "MCP: registered N tool(s) from 1 server(s)" where N >= 40
# Expect NO "Expected response header Content-Type to contain 'text/event-stream', got 'text/html'"
# Expect NO "MCP server 'hermes' failed initial connection"

# 5. End-to-end: a tool call from vc-client hits an upstream.
# In a fresh vc-client session, ask the model to call
# mcp__hermes__<scheme-A-or-B-result>__list_repositories (or any tool).
# Verify the operator-visible name shape first by inspection — grep
# /sse/notifications for 'tools/list' advertises with the chosen prefix
# scheme. The vc-client log line:
grep "tool_call" ~/.hermes/profiles/vc-client/logs/agent.log | tail -5
# Expect: successful response.

# 6. One upstream fails -> proxy keeps serving the rest.
pkill -P $PROXY_PID -f code-review-graph
sleep 2
curl -sS -X POST -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    http://localhost:8000/messages
# Expect: 200 + tools/list result listing the OTHER six upstreams;
#         the dropped upstream is absent but no error response.

# 7. SIGTERM is graceful.
mcp_reverse_proxy.py --port 8000 stop
# Expect: exit=0 within 5s. stderr (or --foreground stdout) shows
#         "shutting down" + per-upstream "closed" lines.

# 8. doctor surfaces upstream failures without refusing to start.
mcp_reverse_proxy.py --port 8000 doctor
# Expect: exit=0; JSON report lists each mcp_servers entry with
#         connected: true|false + initialize error if false.

# Cleanup:
mcp_reverse_proxy.py --port 8000 stop
```

## Rollback

1. Stop the proxy: `mcp_reverse_proxy.py --port 8000 stop` (or
   `pkill -f mcp_reverse_proxy.py`).
2. Revert the vc-client `mcp_servers.hermes.url` to the previous
   value (`http://localhost:9119/sse`) by re-running the
   `talaria config sync vc hermes-vc --add-mcp-serve` command
   with the original `--mcp-serve-port 9119`. The original SSE
   error returns — every `agent.log` line for the hermes
   mcp_server fails again. vc-client sessions continue to
   work; only the MCP tools from the seven upstreams
   disappear.
3. Remove the proxy script:
   `rm ~/.hermes/profiles/hermes-vc/bin/mcp_reverse_proxy.py`.
4. Revert any launcher change in `~/.local/bin/hermes-vc`.

No Talaria state changes need reverting — the
`talaria config sync --add-mcp-serve` writer is
idempotent; re-running it with the previous port
restores the prior config.yaml block.

## Open questions

1. **Prefix scheme (A vs B).** Confirm which
   prefixing scheme Hermes uses by reading
   `hermes/tools/mcp_tool.py` to see whether
   `params.serverKey` is propagated to the SSE
   client. If yes: Scheme A. If no: Scheme B. The
   verification step 5 surfaces whichever scheme
   wins by exercising a tool call end-to-end.
2. **Upstream authentication.** DocumentDB and
   Astro inherit creds from `env:` blocks. The
   proxy inherits them via the same channel.
   Upstream `Authorization` headers (Astro, DeepWiki)
   flow through `streamable_http_client`'s `headers=`
   arg — make sure each upstream config's `headers:`
   block is honoured, not just `env:`.
3. **`mcp_discovery_timeout` bump.** Confirm the
   `mcp_discovery_timeout` increase from 1.5s to
   ~5s is the right knob in vc-client's
   `config.yaml`, or whether the timeout lives
   elsewhere. Verify by reading
   `hermes_cli/mcp_startup.py`
   `_resolve_discovery_timeout` (default
   `DEFAULT_CONFIG['mcp_discovery_timeout']`).
4. **Profile distribution ownership.** If the
   hermes-vc profile is shared via `distribution.yaml`,
   the proxy script needs to be added to
   `distribution_owned:` so it ships to other
   operators. Decide based on whether hermes-vc is
   currently a published distribution (read
   `~/.hermes/profiles/hermes-vc/distribution.yaml`
   if present).
