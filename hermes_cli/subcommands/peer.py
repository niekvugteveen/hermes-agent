"""``hermes peer`` subcommand parser."""

from __future__ import annotations

from typing import Callable

from hermes_cli.subcommands._shared import add_accept_hooks_flag


def build_peer_parser(subparsers, *, cmd_peer: Callable) -> None:
    peer_parser = subparsers.add_parser(
        "peer",
        help="Pair and serve Hermes agent-to-agent (A2A) peers",
        description=(
            "Manage peer pairing and run the A2A MCP HTTP server for "
            "dockerized Hermes instances."
        ),
    )
    peer_sub = peer_parser.add_subparsers(dest="peer_action")

    pair_p = peer_sub.add_parser("pair", help="Pair a remote peer")
    pair_p.add_argument("peer_id", help="Short name for the remote peer (e.g. bob)")
    pair_p.add_argument("url", help="Remote MCP URL (e.g. http://bob-hermes:8765/a2a/mcp)")
    pair_p.add_argument(
        "--token",
        default=None,
        help="Shared bearer token (generated if omitted)",
    )

    peer_sub.add_parser("list", aliases=["ls"], help="List paired remote peers")

    revoke_p = peer_sub.add_parser("revoke", help="Remove a paired remote peer")
    revoke_p.add_argument("peer_id", help="Peer id to revoke")

    token_p = peer_sub.add_parser("token", help="Show or rotate the inbound bearer token")
    token_p.add_argument(
        "--rotate",
        action="store_true",
        help="Generate a new inbound token",
    )
    token_p.add_argument(
        "--set",
        dest="set_token",
        default=None,
        help="Set inbound token to an explicit value",
    )

    serve_p = peer_sub.add_parser(
        "serve",
        help="Run the A2A MCP HTTP server (stdio-less)",
    )
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--path", default="/a2a/mcp")
    serve_p.add_argument("--local-id", default="", dest="local_id")
    serve_p.add_argument("-v", "--verbose", action="store_true")
    add_accept_hooks_flag(serve_p)

    peer_sub.add_parser("pending", help="List requests awaiting human approval")

    status_p = peer_sub.add_parser("status", help="Show one A2A request by id")
    status_p.add_argument("request_id", help="Request id from peer_submit")

    peer_parser.set_defaults(func=cmd_peer)
