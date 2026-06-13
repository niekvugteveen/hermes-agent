---
title: "Hermes agent-to-agent (A2A) — implementation spec"
status: implemented
date: 2026-06-13
updated: 2026-06-13
type: feature
target_repo: hermes-agent
design: docs/design/hermes-a2a-protocol.md
branch: cursor/hermes-a2a-phase3-09b5
---

# Hermes agent-to-agent (A2A) — implementation spec

This document is the **single consolidated implementation plan and specification** for Hermes A2A. It merges the approved design (`docs/design/hermes-a2a-protocol.md`) with what was built, how to configure it, and what remains deferred.

**Implementation status (2026-06-13):** Phases 1–3, U7 slash commands, and U8 e2e smoke are **done** on branch `cursor/hermes-a2a-phase3-09b5`. U4 (skill doc), `peer_events_wait`, `peer_ack`, website docs, and MCP catalog entry are **deferred**.

---

## 1. Summary

Hermes A2A lets two (or more) Hermes instances on a private network exchange structured requests over **MCP over HTTP**. The local agent investigates and drafts a response; the local human approves before anything crosses the peer boundary. v1 supports:

| Type | Direction | Human gates |
|------|-----------|-------------|
| `knowledge_request` | Caller → responder | Responder approves proposed answer |
| `skill_share` | Push or pull | Sender approves outbound artifact; receiver approves inbound install (pull response may skip receiver gate) |
| `set_reminder` | Caller → target host | Target human approves cron job creation |

**Footprint:** dedicated `hermes_a2a/` package, `hermes peer` CLI, gateway hooks, **service-gated** `a2a` toolset (zero schema cost when not in an A2A session). Callers use existing MCP client tools (`mcp_<peer>_peer_submit`, etc.) — no new core tools on every API call.

**Target topology:** two dockerized Hermes containers on a Docker **bridge** network; DNS resolves service names (`bob-hermes:8765`).

---

## 2. Goals and non-goals

### Goals

- Agent stays in the loop on both sides (no webhook `deliver_only` shortcuts).
- Humans see exactly what will be sent or installed before it happens.
- Works across containers via HTTP MCP (`mcp_servers.url` in `config.yaml`).
- A2A traffic uses **isolated sessions** — main conversation prompt cache is untouched.
- Profile-safe state under `get_hermes_home()/peers/`.

### Non-goals (v1)

- Open federation / WAN peers without TLS.
- Real-time streaming chat between agents.
- Automatic skill sync or live config inheritance between profiles.
- Replacing Kanban (same-host worker coordination).
- `peer_events_wait` long-poll (deferred).
- `peer_ack` back to sender after skill install (deferred).

---

## 3. Architecture

```
┌─────────────────────┐         MCP/HTTP          ┌─────────────────────┐
│  Hermes A (Alice)   │ ◄──────────────────────► │  Hermes B (Bob)     │
│                     │   Bearer peer token      │                     │
│  Agent A            │                          │  Agent B            │
│    └─ mcp client    │                          │    └─ a2a MCP srv   │
│       mcp_bob_*     │                          │       peer_submit   │
│                     │                          │                     │
│  Gateway            │                          │  Gateway            │
│    └─ a2a session   │                          │    └─ a2a session   │
│    └─ clarify UI    │                          │    └─ clarify UI    │
└─────────────────────┘                          └─────────────────────┘
         ▲                                                  ▲
    Human Alice                                        Human Bob
```

### Components (as implemented)

| Component | Location | Role |
|-----------|----------|------|
| A2A MCP HTTP server | `hermes_a2a/mcp_server.py`, `hermes_a2a/server.py` | Thin RPC: auth, persist envelope, dispatch to gateway |
| Request store | `hermes_a2a/store.py` | `peers/requests/<id>.json` correlation + status |
| Peer registry | `hermes_a2a/registry.py` | `peers/registry.yaml` — local id, remotes, token hashes, trust |
| Inbound dispatch | `hermes_a2a/dispatch.py` | Callback registry; gateway registers handler at startup |
| A2A context | `hermes_a2a/context.py` | ContextVars: `request_id`, `from_peer`, `type`, `skip_inbound` |
| Gateway orchestrator | `gateway/a2a.py` | Isolated agent turn per inbound envelope |
| Service-gated tools | `tools/a2a_tool.py` | Propose/accept/share/reminder — only when A2A context active |
| Outbound client | `hermes_a2a/client.py` | `peer_push` for skill_share |
| Skill artifacts | `hermes_a2a/artifacts.py` | Bundle/install tiers, checksum, secret stripping |
| Cron proposals | `hermes_a2a/cron_proposal.py` | Parse payload, preview, `create_job` on approve |
| CLI | `hermes_cli/peer.py`, `hermes_cli/subcommands/peer.py` | `hermes peer …` |
| Slash commands | `hermes_cli/a2a_commands.py` | `/peer pending`, `approve`, `deny`, `status` |

### Session model

- Session key: `agent:main:a2a:peer:<from_peer>` (one session per remote peer).
- Inbound requests become a structured **user message** in that session only.
- `skip_memory=True` on A2A agent turns; toolset `["a2a", "terminal", "file", "search", "web"]`.

---

## 4. Wire format

JSON envelopes (logical; stored fields in `RequestStore`):

```json
{
  "schema_version": 1,
  "request_id": "uuid",
  "type": "knowledge_request | skill_share | set_reminder",
  "from_peer": "alice",
  "to_peer": "bob",
  "payload": {},
  "status": "pending | awaiting_human | completed | denied | expired",
  "proposed_response": "...",
  "response": { "status": "answered | denied | created | installed", "...": "..." }
}
```

**Authentication:** `Authorization: Bearer <token>` on every MCP HTTP call.

- **Inbound:** token verified against `registry.yaml` `inbound_token_hash` or env `HERMES_A2A_INBOUND_TOKEN`.
- **Outbound:** token stored in plaintext in `registry.yaml` remotes entry (profile-local); also set in `mcp_servers.<peer>.headers.Authorization`.

Default expiry: **7 days** (`DEFAULT_EXPIRY_DAYS` in `hermes_a2a/constants.py`).

---

## 5. Permission model

### Principle: propose → approve → act

The local agent may use internal tools (calendar, `skill_view`, `read_file`, etc.) **before** asking the human. The gate applies only to **cross-peer effects**.

### Approval choices

| Choice | Constant | Meaning |
|--------|----------|---------|
| Send once | `send once` | Approve this action only |
| Always from this peer | `always from this peer` | Auto-approve future outbound/inbound of this **type** from `from_peer` (stored in `registry.yaml` `trust`) |
| Deny | `deny` | Do not send / install / create |
| Edit | (via clarify free text) | Human revises proposal text, tier, or cron spec |
| Other | clarify "Other" | Free-form clarification |

**Separate always-scopes per type** — e.g. always `knowledge_request` does **not** imply always `set_reminder`.

### Gate matrix

| Flow | Caller human | Responder / target human |
|------|--------------|--------------------------|
| `knowledge_request` | — | Approves proposed answer (`a2a_propose_response`) |
| `skill_share` push | Approves outbound (`a2a_share_skill`) | Approves inbound install (`a2a_accept_skill`) |
| `skill_share` pull | — | Approves outbound bundle; pull **response** may skip inbound approval (`skip_inbound_approval`) |
| `set_reminder` | — | Approves proposed cron (`a2a_propose_reminder`) |

On **deny** for `knowledge_request`: caller `peer_status` returns `{ "status": "denied" }` without payload (opaque default).

---

## 6. Request types

### 6.1 `knowledge_request`

**Request payload:**

```json
{
  "question": "Is your person available Tuesday afternoon?",
  "context": "Scheduling a 1:1",
  "response_format": "text"
}
```

**Flow:**

1. Caller agent: `mcp_<peer>_peer_submit(request_type="knowledge_request", from_peer="<local_id>", payload_json=...)`.
2. Receiver MCP persists request → `dispatch_inbound` → gateway runs A2A agent turn.
3. Receiver agent investigates, calls `a2a_propose_response(proposed_response="...")`.
4. Human approves via clarify (or `/peer approve <id> [answer]`).
5. Caller polls `peer_status(request_id)` → `response.answer`.

**Response payload (completed):**

```json
{
  "status": "answered",
  "answer": "Tuesday after 2pm works."
}
```

### 6.2 `skill_share`

**Artifact tiers** (`hermes_a2a/artifacts.py`):

| Tier | Contents |
|------|----------|
| `reference` | Name + description only |
| `summary` | `SKILL.md` body |
| `full` | `SKILL.md` + `scripts/` + `references/` (secrets stripped) |

**Push payload (via `peer_push`):**

```json
{
  "mode": "push",
  "skill_name": "deploy-checklist",
  "tier": "full",
  "artifact": {
    "files": [{ "path": "SKILL.md", "content_base64": "..." }],
    "checksum": "sha256:..."
  },
  "message": "Thought you'd find this useful."
}
```

**Pull payload (via `peer_submit` type `skill_share`):**

```json
{
  "mode": "pull",
  "skill_name": "deploy-checklist",
  "tier": "summary",
  "message": "Please send your deploy checklist."
}
```

**Tools:**

- Outbound: `a2a_share_skill(peer_id, skill_name, tier, message, responding_to_pull?)`
- Inbound install: `a2a_accept_skill()` (gated on `skill_share` context)

Install path reuses `apply_skill_pending` / skill manager staging patterns.

**Deferred:** `peer_ack` to sender with `{ "status": "installed | declined" }`.

### 6.3 `set_reminder`

**Request payload:**

```json
{
  "message": "Meeting with Alice — 30 min prep",
  "schedule": "2026-06-17T09:00:00",
  "timezone": "America/Los_Angeles",
  "recurrence": null,
  "delivery_hint": "telegram"
}
```

Schedule strings reuse `cron/jobs.parse_schedule` (`30m`, `every monday 9am`, `0 9 * * *`, ISO one-shot).

**Flow:**

1. Caller: `peer_submit(type=set_reminder, ...)`.
2. Target agent drafts job, calls `a2a_propose_reminder(...)`.
3. Human approves → `create_reminder_job` in `hermes_a2a/cron_proposal.py`.
4. Caller `peer_status` → `{ "status": "created", "job_id": "..." }`.

**Security:** `set_reminder` requires per-request human approval unless explicit `always: set_reminder` trust rule for that peer.

---

## 7. MCP tool surface (receiver)

Implemented in `hermes_a2a/mcp_server.py`:

| Tool | Status | Description |
|------|--------|-------------|
| `peer_capabilities` | ✅ | `schema_version`, `peer_id`, `supported_types`, `implemented_types` |
| `peer_submit` | ✅ | `knowledge_request`, `set_reminder`, `skill_share` (pull) |
| `peer_push` | ✅ | `skill_share` push |
| `peer_status` | ✅ | Poll status + `response` |
| `peer_events_wait` | ❌ deferred | Long-poll completion |

HTTP path default: `/a2a/mcp` on port `8765`, bind `0.0.0.0`.

---

## 8. Service-gated agent tools (receiver)

Toolset `a2a` in `toolsets.py` — visible only when `a2a_context_active()`:

| Tool | `check_fn` | Purpose |
|------|------------|---------|
| `a2a_propose_response` | A2A context | Finalize `knowledge_request` answer |
| `a2a_share_skill` | Has remote peers | Outbound skill bundle + `peer_push` |
| `a2a_accept_skill` | Context type `skill_share` | Inbound install after approval |
| `a2a_propose_reminder` | Context type `set_reminder` | Propose + create cron on approve |

Caller side: **no dedicated Hermes tools** — agent uses MCP tools from `mcp_servers` config (see §10).

---

## 9. Human surfaces

### CLI

```bash
hermes peer pair <id> <url> [--token <token>]
hermes peer list
hermes peer revoke <id>
hermes peer token [--rotate | --set <token>]
hermes peer serve [--host 0.0.0.0] [--port 8765] [--path /a2a/mcp]
hermes peer pending
hermes peer status [<request_id>]
```

### Slash commands (CLI + gateway)

| Command | Action |
|---------|--------|
| `/peer pending` | List open inbound requests |
| `/peer status [<id>]` | JSON detail for one request |
| `/peer approve <id> [answer]` | Approve (optional override answer for knowledge) |
| `/peer deny <id>` | Deny without leaking payload |

Handlers: `hermes_cli/a2a_commands.py`; wired in `cli.py`, `gateway/run.py`, `hermes_cli/commands.py`.

---

## 10. Configuration and setup

### 10.1 `config.yaml` (both instances)

```yaml
peers:
  local_id: alice          # or bob — unique per instance
  serve:
    enabled: true          # gateway starts A2A MCP HTTP alongside messaging
    host: 0.0.0.0
    port: 8765
    path: /a2a/mcp

mcp_servers:
  bob:                     # on Alice — outbound to Bob
    url: "http://bob-hermes:8765/a2a/mcp"
    headers:
      Authorization: "Bearer <shared-or-bob-inbound-token>"
    timeout: 300
```

Enable toolset (if not default): `hermes tools` → enable `a2a` for the platform, or:

```yaml
tools:
  cli:
    enabled: [a2a]
  telegram:
    enabled: [a2a]
```

### 10.2 Secrets (`.env` or `hermes peer token`)

| Variable | Purpose |
|----------|---------|
| `HERMES_A2A_INBOUND_TOKEN` | Bearer token peers use to call **this** instance's MCP server |
| `HERMES_A2A_SERVE` | `1` / `true` — opt-in serve when not using `peers.serve.enabled` |
| `HERMES_A2A_HOST` | Override bind host (default `0.0.0.0`) |
| `HERMES_A2A_PORT` | Override port (default `8765`) |
| `HERMES_A2A_PATH` | Override path (default `/a2a/mcp`) |
| `HERMES_A2A_LOCAL_ID` | Override local peer id at serve time |

Generate/set inbound token:

```bash
hermes peer token --rotate
# or
hermes peer token --set my-secret-token
```

### 10.3 Pairing (run on each side)

On **Alice** (knows Bob's URL and Bob's inbound token):

```bash
hermes peer pair bob http://bob-hermes:8765/a2a/mcp --token <bob-inbound-token>
```

On **Bob** (symmetric, for callbacks / skill pull):

```bash
hermes peer pair alice http://alice-hermes:8765/a2a/mcp --token <alice-inbound-token>
```

### 10.4 Dependencies

```bash
pip install 'mcp'   # FastMCP + streamable HTTP
```

Gateway with `peers.serve.enabled: true` starts the A2A server automatically (`gateway/a2a.py` → `start_a2a_on_gateway`). Standalone:

```bash
hermes peer serve --host 0.0.0.0 --port 8765
```

### 10.5 Docker bridge example

See `tests/e2e/a2a/docker-compose.yml` and `docs/design/hermes-a2a-protocol.md` § Docker deployment.

Minimal checklist per container:

1. `peers.local_id` set.
2. `peers.serve.enabled: true` (or `HERMES_A2A_SERVE=1`).
3. `HERMES_A2A_INBOUND_TOKEN` set (same value remote peers use in `Authorization`).
4. `mcp_servers` entries for remotes using **service DNS names**.
5. Gateway running (or `hermes peer serve` for MCP-only smoke).
6. `a2a` toolset enabled for inbound agent turns.

---

## 11. File map (implemented)

```
hermes_a2a/
  __init__.py
  constants.py       # types, statuses, approval constants
  registry.py        # peers/registry.yaml
  store.py           # peers/requests/*.json
  context.py         # ContextVars for tool gating
  dispatch.py        # inbound handler registration
  mcp_server.py      # FastMCP tools + Bearer middleware
  server.py          # gateway/standalone uvicorn lifecycle
  client.py          # outbound peer_push
  artifacts.py       # skill bundle/install
  cron_proposal.py   # set_reminder parse/create

tools/a2a_tool.py    # service-gated a2a_* tools

gateway/a2a.py       # process_a2a_inbound, gateway lifecycle

hermes_cli/
  peer.py            # hermes peer subcommand handlers
  a2a_commands.py    # /peer shared logic
  subcommands/peer.py

tests/hermes_a2a/
  test_peer_registry.py
  test_store.py
  test_a2a_tool.py
  test_skill_share.py
  test_set_reminder.py

tests/hermes_cli/test_a2a_commands.py
tests/gateway/test_a2a_slash_commands.py

tests/e2e/a2a/
  docker-compose.yml
  Dockerfile
  serve_peer.py
  conftest.py
  test_bridge_peers.py
  README.md
```

**On disk (runtime, profile-scoped):**

```
~/.hermes/peers/
  registry.yaml          # local_id, inbound_token_hash, remotes, trust
  requests/<id>.json     # in-flight + completed correlation
```

---

## 12. Implementation units — status

| Unit | Scope | Status | Notes |
|------|-------|--------|-------|
| **U1** | Peer registry + `hermes peer` CLI | ✅ | `pair`, `list`, `revoke`, `token`, `serve`, `pending`, `status` |
| **U2** | A2A MCP HTTP server | ✅ mostly | `peer_submit`, `peer_push`, `peer_status`, `peer_capabilities`; **`peer_events_wait` deferred** |
| **U3** | Gateway orchestrator + `knowledge_request` | ✅ | `gateway/a2a.py`, `a2a_propose_response`; dedicated tests in `test_a2a_tool.py` (not separate `test_a2a_knowledge_request.py`) |
| **U4** | Caller skill doc | ❌ | `skills/autonomous-ai-agents/hermes-a2a/SKILL.md` not shipped |
| **U5** | `skill_share` two-sided | ✅ mostly | artifacts + push/pull; **`peer_ack` deferred** |
| **U6** | `set_reminder` | ✅ | `cron_proposal.py`, `a2a_propose_reminder` |
| **U7** | `/peer` slash commands | ✅ | CLI + gateway |
| **U8** | E2E docker smoke | ✅ | In-process e2e default; docker gated on `HERMES_A2A_E2E_DOCKER=1` |

### Suggested merge order (historical)

1. U1 + U2 — pairing + MCP server  
2. U3 + U4 — knowledge_request + skill doc  
3. U5 — skill share  
4. U6 — set_reminder  
5. U7 + U8 — slash commands + e2e  

---

## 13. Verification

### Unit / integration tests

```bash
scripts/run_tests.sh tests/hermes_a2a/ -q
scripts/run_tests.sh tests/hermes_cli/test_a2a_commands.py -q
scripts/run_tests.sh tests/gateway/test_a2a_slash_commands.py -q
```

### E2E (in-process Bob server)

```bash
pytest tests/e2e/a2a/test_bridge_peers.py -m e2e -q -p no:xdist
```

### E2E (Docker bridge, optional)

```bash
docker compose -f tests/e2e/a2a/docker-compose.yml up --build
HERMES_A2A_E2E_DOCKER=1 pytest tests/e2e/a2a/test_bridge_peers.py -m e2e_docker -o 'addopts=' -q
```

See `tests/e2e/a2a/README.md` for manual pairing on `127.0.0.1:18765`.

### Manual smoke script

1. Start two Hermes profiles or containers on a bridge network.  
2. Set inbound tokens; `hermes peer pair` on both sides.  
3. Add remote to `mcp_servers` on caller.  
4. From caller agent: submit `knowledge_request`; approve on receiver (`/peer approve` or clarify).  
5. Poll `peer_status` on caller.  
6. Repeat for `skill_share` push and `set_reminder`.

### Invariants (tests enforce)

- Denied `knowledge_request` leaks no answer payload to caller.
- Always-trust is scoped per `(peer_id, request_type)`.
- Skill install runs through artifact checksum + secret stripping.
- A2A sessions do not append to main `agent:main` session history.

---

## 14. Error handling

| Condition | Behavior |
|-----------|----------|
| Bad / missing Bearer | HTTP 401 |
| Unknown `request_id` | `peer_status` → `{"error":"request not found"}` |
| Expired request | `status: expired` after `expires_at` |
| Malformed skill artifact | Reject at staging; no partial write |
| Cron create failure after approve | Error recorded; human sees failure on target host |
| Peer offline | Caller agent reports unreachable (client/MCP error) |

---

## 15. Deferred / follow-up

| Item | Rationale |
|------|-----------|
| `peer_events_wait` long-poll | Reuse EventBridge pattern from `mcp_serve.py`; polling works for v1 |
| `peer_ack` after skill install | Caller learns install outcome asynchronously |
| U4 skill `hermes-a2a` | Documents MCP caller workflow for agents |
| `tests/hermes_a2a/test_mcp_server.py` | Dedicated HTTP auth roundtrip (partially covered by e2e) |
| `website/docs/user-guide/features/a2a.md` | User-facing docs |
| MCP catalog entry `hermes-a2a` | Optional distribution path |
| TLS / WAN / rate limits | v1.1 |

---

## 16. References

- Design (approved): `docs/design/hermes-a2a-protocol.md`
- Original plan (superseded by this doc): `docs/plans/2026-06-13-hermes-a2a-protocol-plan.md`
- MCP messaging bridge (permission pattern): `mcp_serve.py`
- MCP HTTP client (caller): `tools/mcp_tool.py`
- Skill staging: `tools/skill_manager_tool.py`
- Cron: `cron/jobs.py`, `tools/cronjob_tools.py`
- AGENTS.md — Footprint Ladder, prompt caching, profile-safe paths
