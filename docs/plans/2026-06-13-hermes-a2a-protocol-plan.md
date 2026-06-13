---
title: "feat: Hermes agent-to-agent (A2A) protocol over MCP"
status: superseded
date: 2026-06-13
type: feature
target_repo: hermes-agent
design: docs/design/hermes-a2a-protocol.md
superseded_by: docs/plans/2026-06-13-hermes-a2a-implementation-spec.md
---

# feat: Hermes agent-to-agent (A2A) protocol over MCP

> **Superseded.** The full implementation plan and specification live in
> [`2026-06-13-hermes-a2a-implementation-spec.md`](./2026-06-13-hermes-a2a-implementation-spec.md).
> That document merges the approved design, build status (U1–U8), configuration,
> file map, verification commands, and deferred follow-ups.

## Quick links

| Document | Purpose |
|----------|---------|
| [Implementation spec](./2026-06-13-hermes-a2a-implementation-spec.md) | **Canonical** plan + spec + setup |
| [Design spec](../design/hermes-a2a-protocol.md) | Approved architecture and wire format |
| [E2E README](../../tests/e2e/a2a/README.md) | Docker bridge smoke tests |

## One-line summary

Structured peer communication between Hermes instances over **MCP/HTTP** with human approval gates for `knowledge_request`, `skill_share`, and `set_reminder` — implemented on `cursor/hermes-a2a-phase3-09b5`.
