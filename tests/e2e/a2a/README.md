# A2A Docker bridge smoke tests

Optional end-to-end validation for two Hermes A2A peers on a Docker **bridge**
network — the same topology described in `docs/design/hermes-a2a-protocol.md`.

## Automated (no Docker)

Runs a local Bob MCP server in a subprocess with an e2e auto-approve handler:

```bash
pytest tests/e2e/a2a/test_bridge_peers.py -m e2e -q
```

## Manual Docker smoke

```bash
docker compose -f tests/e2e/a2a/docker-compose.yml up --build
```

Bob is exposed on `http://127.0.0.1:18765/a2a/mcp` (token: `e2e-shared-secret`).
Alice is on port `18766` for bidirectional experiments.

Pair from a host checkout:

```bash
export HERMES_A2A_INBOUND_TOKEN=e2e-shared-secret
hermes peer token --set e2e-shared-secret   # on each instance if using full Hermes
hermes peer pair bob http://127.0.0.1:18765/a2a/mcp --token e2e-shared-secret
```

Then use MCP `peer_submit` / `peer_status` (or an agent with Bob in `mcp_servers`).

## CI docker job (optional)

```bash
HERMES_A2A_E2E_DOCKER=1 pytest tests/e2e/a2a/test_bridge_peers.py -m e2e_docker -o 'addopts=' -q
```

Requires Docker and pulls/builds the slim `tests/e2e/a2a/Dockerfile` image.
