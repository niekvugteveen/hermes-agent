FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only what the A2A MCP server needs (not the full Hermes image).
RUN pip install --no-cache-dir \
    'mcp>=1.2,<2' \
    'uvicorn>=0.30,<1' \
    'httpx>=0.28,<1' \
    'pyyaml>=6,<7'

COPY hermes_constants.py /app/
COPY hermes_a2a /app/hermes_a2a
COPY tests/e2e/a2a/serve_peer.py /app/serve_peer.py

ENV PYTHONPATH=/app
ENV HERMES_HOME=/data
ENV HERMES_A2A_INBOUND_TOKEN=e2e-shared-secret
ENV HERMES_PEER_LOCAL_ID=bob

RUN mkdir -p /data

EXPOSE 8765

HEALTHCHECK --interval=2s --timeout=3s --retries=30 \
    CMD curl -fsS -o /dev/null -H "Authorization: Bearer e2e-shared-secret" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"health","version":"1"}}}' \
        http://127.0.0.1:8765/a2a/mcp || exit 1

CMD ["python", "/app/serve_peer.py", "--host", "0.0.0.0", "--port", "8765", "--local-id", "bob", "--auto-approve"]
