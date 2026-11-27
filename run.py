"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import argparse
import atexit
import socket
import sys

import uvicorn

from app import db, llamacpp_manager


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI binding options (`--lan`, `--host`, `--port`)."""
    parser = argparse.ArgumentParser(description="Shiori Interactive Japanese Reader")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Listen on all network interfaces (0.0.0.0) so your phone on the same Wi-Fi can connect.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host address (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind TCP port (default: 8000).",
    )
    args = parser.parse_args(argv)
    if args.lan:
        args.host = "0.0.0.0"
    return args


def detect_lan_ip() -> str | None:
    """Best-effort detection of the local LAN IPv4 address without sending network traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None


def main() -> None:
    args = parse_args(sys.argv[1:])
    db.init_db()
    atexit.register(llamacpp_manager.stop_managed_server)

    try:
        if args.host == "0.0.0.0":
            lan_ip = detect_lan_ip()
            print(
                f"Starting Shiori on http://127.0.0.1:{args.port} "
                f"(LAN open; local LLM loads in background if enabled)"
            )
            if lan_ip:
                print(f"Open on your phone (same Wi-Fi): http://{lan_ip}:{args.port}")
        else:
            print(
                f"Starting Shiori at http://{args.host}:{args.port} "
                "(local LLM loads in background if enabled)"
            )
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
    finally:
        llamacpp_manager.stop_managed_server()


if __name__ == "__main__":
    main()
