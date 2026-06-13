"""``hermes peer`` CLI — pair and serve the A2A MCP surface."""

from __future__ import annotations

from hermes_a2a.registry import PeerRegistry
from hermes_a2a.server import run_standalone
from hermes_a2a.store import RequestStore


def cmd_peer(args) -> int:
    action = getattr(args, "peer_action", None)
    if action in {None, "help"}:
        print("Usage: hermes peer <pair|list|revoke|token|serve|pending|status>")
        return 0

    registry = PeerRegistry()
    store = RequestStore()

    if action == "pair":
        peer_id = args.peer_id.strip().lower()
        url = args.url.strip()
        token = getattr(args, "token", None)
        issued = registry.pair_remote(peer_id, url, token=token)
        print(f"Paired remote peer '{peer_id}' → {url}")
        print(f"Bearer token for outbound calls: {issued}")
        print(
            f"On the remote host, set the same value as inbound token "
            f"(config or `hermes peer token --set ...`)."
        )
        return 0

    if action == "list":
        local_id = registry.get_local_id() or "(unset)"
        print(f"Local peer id: {local_id}")
        remotes = registry.list_remotes()
        if not remotes:
            print("No remote peers configured.")
            return 0
        for remote in remotes:
            print(f"  {remote['peer_id']}: {remote['url']}")
        return 0

    if action == "revoke":
        if registry.revoke_remote(args.peer_id):
            print(f"Revoked peer '{args.peer_id}'.")
            return 0
        print(f"Peer '{args.peer_id}' was not configured.")
        return 1

    if action == "token":
        if getattr(args, "rotate", False):
            token = registry.rotate_inbound_token()
            print("Rotated inbound token:")
            print(token)
            return 0
        if getattr(args, "set_token", None):
            registry.set_inbound_token(args.set_token)
            print("Inbound token updated.")
            return 0
        try:
            token = registry.ensure_inbound_token()
        except RuntimeError as exc:
            print(str(exc))
            return 1
        print("Inbound bearer token (give this to remote peers):")
        print(token)
        return 0

    if action == "serve":
        host = getattr(args, "host", None) or "0.0.0.0"
        port = int(getattr(args, "port", None) or 8765)
        path = getattr(args, "path", None) or "/a2a/mcp"
        local_id = registry.get_local_id() or getattr(args, "local_id", "") or "hermes"
        registry.set_local_id(local_id)
        registry.ensure_inbound_token()
        print(
            f"Serving A2A MCP on http://{host}:{port}{path} "
            f"(peer id={local_id})"
        )
        run_standalone(
            host=host,
            port=port,
            path=path,
            local_id=local_id,
            verbose=bool(getattr(args, "verbose", False)),
        )
        return 0

    if action == "pending":
        from hermes_cli.a2a_commands import format_pending_list

        print(format_pending_list(store))
        return 0

    if action == "status":
        from hermes_cli.a2a_commands import format_request_status

        print(format_request_status(store, args.request_id))
        return 0 if store.get(args.request_id) else 1

    print(f"Unknown peer action: {action}")
    return 1
