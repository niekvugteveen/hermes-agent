# Hermes Agent-to-Agent (A2A) Protocol — Design Spec

**Status:** Draft for review  
**Date:** 2026-06-13  
**Authors:** Brainstorm session (user + agent)

## Summary

A structured **agent-to-agent communication protocol** for dockerized (or otherwise networked) Hermes instances. Transport is **MCP over HTTP** (not webhooks — webhooks bypass the agent or lack permission gates). Every outbound payload is **proposed by the local agent, then approved by the local human** before it crosses the peer boundary. Inbound **push** operations (skill share, cross-agent reminders) require a second approval on the receiver's side.

### Supported request types (v1)

| Type | Example | Primary approval |
|------|---------|------------------|
| `knowledge_request` | "Is Bob free Tuesday?" | Responder proposes answer → responder's human approves send |
| `skill_share` | Bob shares `deploy-checklist` with Alice | Outbound: Bob approves send. Inbound: Alice approves install |
| `set_reminder` | "Remind Bob Tuesday 9am about the meeting" | Target host proposes cron job → target human approves create |

## Goals

- Agent stays in the loop on both sides (no `deliver_only` webhook shortcuts).
- Humans see **exactly what will be sent or installed** before it happens.
- Works across Docker containers via HTTP MCP (`mcp_servers.url` already supported).
- Minimal core footprint: MCP server extension + gateway approval hooks + CLI pairing + skill (Footprint Ladder: MCP catalog / plugin, not new core tools).

## Non-goals (v1)

- Open federation (any agent on the internet).
- Real-time streaming chat between agents.
- Automatic skill sync / live config inheritance between profiles.
- Replacing Kanban (same-host worker coordination).

## Architecture

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
│    └─ inbound queue │                          │    └─ outbound gate │
│    └─ clarify UI    │                          │    └─ clarify UI    │
└─────────────────────┘                          └─────────────────────┘
         ▲                                                  ▲
         │                                                  │
    Human Alice                                        Human Bob
```

### Components

1. **`hermes-a2a` MCP server** — HTTP listener (extend `hermes mcp serve` or sibling process). Thin RPC: validate token, enqueue envelope, return `request_id`. Does not run the LLM.
2. **Gateway A2A orchestrator** — Wakes dedicated `a2a:<peer>` sessions, drives agent turns, presents approval prompts via existing `clarify` / gateway approval UX.
3. **Peer store** — `~/.hermes/peers/` (profile-scoped via `get_hermes_home()`):
   - `registry.yaml` — paired peers (id, display name, url, token hash, trust rules)
   - `outbound_pending/` — drafts awaiting human send approval
   - `inbound_pending/` — offers awaiting human accept (push only)
   - `requests/` — in-flight correlation state
4. **CLI** — `hermes peer pair`, `hermes peer list`, `hermes peer revoke`, `hermes peer serve` (HTTP wrapper).
5. **Skill** — `skills/autonomous-ai-agents/hermes-a2a/` documents protocol for agents using peer tools.

## Wire format

All messages are JSON envelopes:

```json
{
  "schema_version": 1,
  "envelope_id": "uuid",
  "correlation_id": "uuid",
  "type": "knowledge_request | skill_share | set_reminder | ack",
  "phase": "request | propose | response | ack",
  "from_peer": "alice",
  "to_peer": "bob",
  "created_at": "2026-06-13T12:00:00Z",
  "expires_at": "2026-06-20T12:00:00Z",
  "payload": {}
}
```

Authentication: `Authorization: Bearer <peer_token>` on every MCP HTTP call. Tokens are generated at pair time; stored hashed in registry.

## Permission model

### Principle: propose → approve → act

The local agent may use any internal tools (calendar, `skill_view`, `read_file`, `cronjob` list, etc.) **before** asking the human. The gate applies only to **cross-peer effects**.

### Approval choices (consistent across types)

| Choice | Meaning |
|--------|---------|
| **Send once** | Approve this outbound action only |
| **Always from this peer** | Auto-approve future outbound of this `type` from `from_peer` (scoped; stored in `registry.yaml`) |
| **Deny** | Do not send / do not install / do not create job |
| **Edit** | Human revises proposed text, artifact tier, or cron spec; then send |
| **Other** | Free-form clarification (reuses `clarify` "Other" path) |

Separate "always" scopes per type — e.g. always-accept `knowledge_request` from Alice does **not** imply always-accept `set_reminder`.

### Gate matrix

| Flow | Caller human | Responder / target human |
|------|--------------|--------------------------|
| `knowledge_request` (pull) | — | Approves **proposed response** before send |
| `skill_share` (push) | Approves **proposed artifact** before send | Approves **inbound install** |
| `skill_share` (pull: "send me skill X") | — | Approves **proposed artifact** before send |
| `set_reminder` (cross-agent) | — | Approves **proposed cron job** before create |

On **deny** for `knowledge_request`: caller receives `{ "status": "denied" }` with no payload (opaque default). Optional `denial_message` only if target human explicitly approves sending a reason.

## Request type details

### 1. `knowledge_request`

**Purpose:** Ask the peer's agent to answer a question using the peer's context (calendar, memory, files the peer's human has access to).

**Request payload:**

```json
{
  "question": "Is your person available Tuesday afternoon?",
  "context": "Scheduling a 1:1 with Alice",
  "response_format": "text"
}
```

**Flow:**

1. Agent A calls `mcp_bob_peer_submit(type=knowledge_request, payload=...)`.
2. Agent B's A2A session receives the request; Agent B investigates privately.
3. Agent B drafts `proposed_response` (text).
4. Human B sees: *"Alice's agent asks: … I propose to reply: 'Tuesday after 2pm works.' Send?"*
5. On approve → `peer_respond` returns answer to A (poll or `events_wait`).
6. Agent A relays answer to Human A.

**Response payload:**

```json
{
  "status": "answered | denied",
  "answer": "Tuesday after 2pm works.",
  "denial_message": null
}
```

### 2. `skill_share`

**Purpose:** Transfer a skill (or subset) from one Hermes instance to another.

**Artifact tiers** (proposer picks default; human can downgrade in Edit):

| Tier | Contents |
|------|----------|
| `reference` | Name + description + pointer ("invoke via peer") — no files |
| `summary` | `SKILL.md` body only |
| `full` | `SKILL.md` + `scripts/` + `references/` (no secrets, no `.env`) |

**Push payload:**

```json
{
  "skill_name": "deploy-checklist",
  "tier": "full",
  "artifact": {
    "files": [
      { "path": "SKILL.md", "content_base64": "..." },
      { "path": "scripts/deploy.sh", "content_base64": "..." }
    ],
    "checksum": "sha256:..."
  },
  "message": "Bob thought you'd find this useful."
}
```

**Push flow:**

1. Human B asks Agent B to share skill with Alice.
2. Agent B builds artifact; Human B approves outbound.
3. `peer_push` → Agent A inbound queue.
4. Agent A shows preview; Human A approves install.
5. On accept → stage via existing `write_approval` / `apply_skill_pending` pattern → `~/.hermes/skills/<name>/`.
6. `ack` to B: `{ "status": "installed | declined" }`.

**Pull flow** (Alice asks for a skill): same as push from step 2 on B's side; Alice initiated so no B outbound approval for *initiating*, but B still approves what leaves. Alice inbound approval optional on pull (default: skip — she asked).

### 3. `set_reminder`

**Purpose:** Ask the peer's agent to create a **cron job on the peer's Hermes** that reminds the peer's human.

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

Schedule strings reuse existing `cron/jobs.parse_schedule` formats (`30m`, `every monday 9am`, `0 9 * * *`, ISO one-shot).

**Flow:**

1. Agent A calls `peer_submit(type=set_reminder, ...)`.
2. Agent B drafts **proposed job** (resolved schedule, delivery platform, final prompt text).
3. Human B sees: *"Alice's agent wants to set a reminder: [schedule] — '[message]'. Create cron job?"*
4. On approve → Agent B calls internal `cronjob` create (not exposed to peer directly).
5. Response to A: `{ "status": "created | denied", "job_id": "..." }`.

**Security:** `set_reminder` always requires per-request human approval unless Human B has explicitly set `always: set_reminder` for that peer. Never bundled with `knowledge_request` always-trust.

## MCP tool surface

Exposed by receiver's `hermes-a2a` MCP server:

| Tool | Description |
|------|-------------|
| `peer_submit` | Submit `knowledge_request` or `set_reminder`; returns `request_id` |
| `peer_push` | Submit `skill_share` (push); returns `request_id` |
| `peer_status` | Poll `request_id` → `pending \| awaiting_human \| completed \| denied \| expired` + result |
| `peer_events_wait` | Long-poll for completion (reuse EventBridge pattern from `mcp_serve.py`) |
| `peer_capabilities` | List supported types + peer id |

Inbound management is **gateway-native** (slash command + clarify), not MCP-from-outside:

- `/peer pending` — list inbound/outbound awaiting human
- `/peer approve <id>` / `/peer deny <id>` — resolve from messaging surface

Hermes-as-client config (`config.yaml`):

```yaml
mcp_servers:
  bob:
    url: "http://bob-hermes:8765/a2a/mcp"
    headers:
      Authorization: "Bearer <token-from-pairing>"
    timeout: 300
```

## Docker deployment

Each container:

1. Runs gateway (`hermes gateway` or equivalent).
2. Exposes A2A MCP HTTP on a published port (e.g. `8765`).
3. Pairs once: `hermes peer pair alice http://alice-hermes:8765/a2a/mcp <token>`.

`hermes mcp serve` today is **stdio-only**; v1 implementation adds `--transport http --host 0.0.0.0 --port 8765` to the a2a server (or documents FastMCP HTTP sidecar).

Network: Docker bridge DNS (`bob-hermes`, `alice-hermes`). TLS optional v1.1 (reverse proxy); v1 can be Bearer token on private network.

## Session model

- Dedicated session key per peer: `a2a:peer:<peer_id>`.
- Inbound requests append to session as structured user messages (preserves prompt caching in *other* sessions — A2A session is isolated).
- Does not mutate main Telegram/CLI conversation history.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Unknown peer / bad token | MCP 401 |
| Peer offline | `peer_status: unreachable`; Agent A informs human |
| Expired `request_id` | `status: expired` after `expires_at` (default 7d) |
| Malformed skill artifact | Reject at inbound staging; never write partial skill |
| Cron create failure | Return error to caller; Human B already approved — show failure in B's UI |

## Testing strategy

- Hermetic: two temp `HERMES_HOME` dirs, two HTTP MCP servers in-process, mock humans via `resolve_gateway_clarify` in tests.
- E2E: docker-compose with two agents, one `knowledge_request` + one `skill_share` + one `set_reminder`.
- Invariants: denied requests leak no payload; always-trust is per-type; skill install matches `skills_guard` scan.

## Implementation phases

### Phase 1 — Foundation
- Peer registry + `hermes peer pair` CLI
- HTTP A2A MCP server with `peer_submit`, `peer_status`, `peer_capabilities`
- Gateway A2A session + outbound approval for `knowledge_request`

### Phase 2 — Skill share
- Artifact bundling + tier selection
- Two-sided approval + `apply_skill_pending` integration

### Phase 3 — Reminders
- `set_reminder` proposal UI showing cron preview
- `cronjob` create on approve + ack to caller

### Phase 4 — Polish
- `events_wait` long-poll
- `/peer` slash commands
- Optional MCP catalog entry `hermes-a2a`
- Skill documentation

## Open questions (deferred)

- TLS/mTLS for non-Docker WAN peers.
- Pull skill share: require Alice inbound confirm? (Default: no.)
- Rate limits per peer (requests/hour).

## References

- `mcp_serve.py` — existing MCP messaging bridge + EventBridge + permissions pattern
- `tools/mcp_tool.py` — HTTP MCP client (caller side)
- `tools/skill_manager_tool.py` — `write_approval` staging for inbound skills
- `tools/cronjob_tools.py` — reminder execution surface
- `gateway/platforms/webhook.py` — explicitly **not** used (bypasses agent)
- AGENTS.md Footprint Ladder — MCP catalog over core tools
