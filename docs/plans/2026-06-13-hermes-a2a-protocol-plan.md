---
title: "feat: Hermes agent-to-agent (A2A) protocol over MCP"
status: active
date: 2026-06-13
type: feature
target_repo: hermes-agent
design: docs/design/hermes-a2a-protocol.md
---

# feat: Hermes agent-to-agent (A2A) protocol over MCP

## Summary

Implement structured peer communication between two dockerized Hermes instances on a **Docker bridge network** (same host). Transport is **MCP over HTTP**. Agents propose outbound actions; humans approve before data crosses the peer boundary. v1 supports `knowledge_request`, `skill_share`, and `set_reminder`.

Design spec (approved): `docs/design/hermes-a2a-protocol.md`

---

## Requirements

- R1. Peer pairing with Bearer token auth; registry in `get_hermes_home()/peers/`.
- R2. HTTP MCP server reachable at `http://<container-name>:8765/a2a/mcp` on Docker bridge.
- R3. `knowledge_request`: responder agent proposes answer → human approves → caller receives response.
- R4. `skill_share` (push): sender approves artifact outbound; receiver approves inbound install.
- R5. `skill_share` (pull): sender approves outbound only (requester asked).
- R6. `set_reminder`: target agent proposes cron job → target human approves → job created via `cronjob` tool.
- R7. Always-trust scoped per `(peer_id, request_type)`; `set_reminder` never inherits from `knowledge_request`.
- R8. Denied requests return opaque `{ status: denied }` unless human approves a denial message.
- R9. A2A traffic uses isolated session keys `a2a:peer:<id>` — no cache break in main sessions.
- R10. No new core model tools; footprint = plugin/MCP server + CLI + skill + gateway hooks.

---

## Key Technical Decisions

- **Separate module `hermes_a2a/`** (or `plugins/hermes-a2a/`) rather than bloating `mcp_serve.py` (messaging bridge stays for Cursor-style clients).
- **HTTP via FastMCP** (`mcp.server.fastmcp`) streamable HTTP — same dependency as existing MCP stack.
- **Gateway orchestrator** wakes A2A sessions; approval UX reuses `clarify_gateway.py` + existing once/always/deny pattern from `mcp_serve` permissions.
- **Skill install** reuses `write_approval` / `apply_skill_pending` from `skill_manager_tool.py`.
- **Reminder create** reuses `cron/jobs.create_job` via internal call after approval (not exposed to peer MCP).
- **Config**: `peers:` section in `config.yaml` (not new `HERMES_*` env vars for behavior); bridge only `HERMES_A2A_SERVE` / port for Docker convenience if needed.

---

## Implementation Units

### U1. Peer registry + CLI

**Files:** `hermes_cli/peer.py`, `hermes_cli/config.py` (DEFAULT_CONFIG `peers:`), `hermes_cli/main.py` (wire subcommand)

**Tasks:**
- `PeerRegistry` — load/save `peers/registry.yaml` (id, name, url, token_hash, trust_rules)
- `hermes peer pair <id> <url> [--token]` — generate or accept token, write registry + print peer URL for reverse pair
- `hermes peer list`, `hermes peer revoke <id>`, `hermes peer show <id>`
- Token verify helper for MCP auth middleware

**Tests:** `tests/hermes_cli/test_peer_registry.py` — hermetic `HERMES_HOME`, pair/list/revoke, token hash verify

---

### U2. A2A MCP HTTP server

**Files:** `hermes_a2a/mcp_server.py`, `hermes_a2a/store.py`, `hermes_cli/subcommands/peer.py` (`hermes peer serve`)

**Tasks:**
- FastMCP HTTP on `0.0.0.0:8765`, path `/a2a/mcp`
- Tools: `peer_submit`, `peer_push`, `peer_status`, `peer_capabilities`, `peer_events_wait`
- Validate Bearer token against registry (inbound) or env `HERMES_A2A_INBOUND_TOKEN`
- Persist envelopes to `peers/requests/<id>.json`; emit bridge events for long-poll
- `peer_capabilities` returns `schema_version`, local peer id, supported types

**Tests:** `tests/hermes_a2a/test_mcp_server.py` — aiohttp test client, 401 without token, submit + status roundtrip

---

### U3. Gateway A2A orchestrator + outbound approval

**Files:** `gateway/a2a.py`, `gateway/run.py` (hook inbound), `tools/clarify_gateway.py` (extend or `gateway/a2a_approval.py`)

**Tasks:**
- On MCP `peer_submit`/`peer_push` received → enqueue → schedule agent turn on `a2a:peer:<from_peer>` session
- Inject structured user message with envelope JSON
- Agent system hint (session-only): investigate, draft proposal, call internal `a2a_propose_outbound` tool
- **Internal service-gated tool** `a2a_propose_outbound` — stages to `peers/outbound_pending/`, triggers clarify to human with Send once / Always / Deny / Edit / Other
- On approve → MCP response path updates request → caller can `peer_status`
- Trust rules: "always" skips clarify for matching type

**Tests:** `tests/gateway/test_a2a_knowledge_request.py` — mock clarify approve/deny, assert no leak on deny

---

### U4. Caller-side MCP integration (no new tools)

**Files:** skill only initially — `skills/autonomous-ai-agents/hermes-a2a/SKILL.md`

**Tasks:**
- Document: add peer to `mcp_servers` with container URL
- Agent uses `mcp_<peer>_peer_submit` / `mcp_<peer>_peer_status` discovered automatically
- Skill prose: when to use each type, permission expectations, Docker bridge hostnames

**Tests:** `tests/skills/test_hermes_a2a_skill.py` — description length, section order

---

### U5. Skill share (two-sided)

**Files:** `hermes_a2a/artifacts.py`, extend `gateway/a2a.py`, hook `apply_skill_pending`

**Tasks:**
- `bundle_skill(name, tier)` → artifact JSON + checksum; strip secrets
- Outbound approval shows file list + size
- Inbound `peer_push` → `peers/inbound_pending/` → clarify to receiver → `apply_skill_pending` on approve
- `peer_ack` back to sender with installed/declined

**Tests:** `tests/hermes_a2a/test_skill_share.py` — push two-sided, pull one-sided outbound gate, skills_guard scan on install

---

### U6. Set reminder

**Files:** extend `gateway/a2a.py`, `hermes_a2a/cron_proposal.py`

**Tasks:**
- Parse `set_reminder` payload; agent drafts job via `parse_schedule`
- Clarify shows human-readable cron preview (schedule, message, delivery)
- On approve → `create_job(...)` from `cron/jobs.py`
- Return `job_id` to caller via `peer_status`
- Never auto-approve unless explicit `always: set_reminder` in trust rules

**Tests:** `tests/hermes_a2a/test_set_reminder.py` — approve creates job in temp HERMES_HOME; deny creates nothing

---

### U7. Slash commands + Docker docs

**Files:** `hermes_cli/commands.py`, `cli.py`, `gateway/run.py`, `docs/design/hermes-a2a-protocol.md` (done), `website/docs/user-guide/features/a2a.md` (optional follow-up)

**Tasks:**
- `/peer pending`, `/peer approve <id>`, `/peer deny <id>`
- `hermes peer serve --host 0.0.0.0 --port 8765` for Docker ENTRYPOINT sidecar
- Example `docker-compose.yml` fragment in design doc (done)

**Tests:** `tests/gateway/test_a2a_slash_commands.py`

---

### U8. E2E docker-compose smoke (optional CI job)

**Files:** `tests/e2e/a2a/docker-compose.yml`, `tests/e2e/a2a/test_bridge_peers.py`

**Tasks:**
- Two services on `bridge` network
- One `knowledge_request` roundtrip with scripted clarify responses
- Gated behind `@pytest.mark.e2e` — not required for merge if unit coverage is strong

---

## Suggested merge order

1. U1 → U2 (pairing + MCP server, no agent yet)
2. U3 → U4 (knowledge_request end-to-end + skill doc)
3. U5 (skill share)
4. U6 (set_reminder)
5. U7 → U8 (polish + optional e2e)

---

## Verification checklist

```bash
# Unit
scripts/run_tests.sh tests/hermes_cli/test_peer_registry.py -q
scripts/run_tests.sh tests/hermes_a2a/ -q
scripts/run_tests.sh tests/gateway/test_a2a_knowledge_request.py -q

# Manual Docker smoke (same host bridge)
docker compose -f tests/e2e/a2a/docker-compose.yml up -d
# pair, send knowledge_request from alice CLI, approve on bob Telegram/CLI
```

---

## Out of scope (this plan)

- TLS / WAN peers
- Rate limiting per peer
- MCP catalog packaging (can follow as optional-mcp entry)
- Inbound approval on pull skill share
