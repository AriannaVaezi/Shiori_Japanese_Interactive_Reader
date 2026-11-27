"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import argparse
import atexit
import os
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


def _is_usable_lan_ipv4(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0":
        return False
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _is_private_lan_ipv4(ip: str) -> bool:
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            return 16 <= second <= 31
        except (IndexError, ValueError):
            return False
    return False


def detect_lan_ips() -> list[str]:
    """Best-effort ordered list of local LAN IPv4 addresses without sending packets."""
    routed: list[str] = []
    for target in (("10.255.255.255", 1), ("1.1.1.1", 80), ("8.8.8.8", 80)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            ip = sock.getsockname()[0]
            if _is_usable_lan_ipv4(ip) and ip not in routed:
                routed.append(ip)
        except OSError:
            pass
        finally:
            sock.close()

    host_ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if _is_usable_lan_ipv4(ip) and ip not in host_ips:
                host_ips.append(ip)
        fallback_ip = socket.gethostbyname(hostname)
        if _is_usable_lan_ipv4(fallback_ip) and fallback_ip not in host_ips:
            host_ips.append(fallback_ip)
    except OSError:
        pass

    merged: list[str] = []
    for ip in routed + host_ips:
        if ip not in merged:
            merged.append(ip)

    # Keep primary outbound routed IPs first, then private RFC1918 LAN IPs
    routed_set = set(routed)
    merged.sort(key=lambda ip: (0 if ip in routed_set else 1, 0 if _is_private_lan_ipv4(ip) else 1))
    return merged


def detect_lan_ip() -> str | None:
    """Return the primary local LAN IPv4 address, if detected."""
    ips = detect_lan_ips()
    return ips[0] if ips else None


def format_startup_banner(host: str, port: int, lan_ips: list[str] | None = None) -> str:
    """Build an easy-to-spot terminal banner with local and phone Wi-Fi URLs."""
    bar = "=" * 62
    lines = ["", bar]
    if host in ("0.0.0.0", "::"):
        ips = detect_lan_ips() if lan_ips is None else lan_ips
        lines.append("  Shiori is ready! (LAN access enabled)")
        lines.append(f"  Desktop:                         http://127.0.0.1:{port}")
        if ips:
            for ip in ips:
                lines.append(f"  Open on your phone (same Wi-Fi): http://{ip}:{port}")
        else:
            lines.append(
                f"  Phone (same Wi-Fi):              Could not auto-detect LAN IP; check `ipconfig` for IPv4 + :{port}"
            )
    else:
        lines.append(f"  Shiori is ready at: http://{host}:{port}")
        lines.append("  Phone / Wi-Fi access is OFF (local only).")
        lines.append("  To open on your phone (same Wi-Fi), run:")
        lines.append(f"    uv run python run.py --lan" + (f" --port {port}" if port != 8000 else ""))
    lines.extend([bar, ""])
    return "\n".join(lines)


def main() -> None:
    args = parse_args(sys.argv[1:])
    db.init_db()
    atexit.register(llamacpp_manager.stop_managed_server)

    os.environ["SHIORI_BIND_HOST"] = args.host
    os.environ["SHIORI_BIND_PORT"] = str(args.port)
    try:
        print(
            f"Starting Shiori on http://{args.host}:{args.port} "
            "(local LLM loads in background if enabled)...",
            flush=True,
        )
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
    finally:
        os.environ.pop("SHIORI_BIND_HOST", None)
        os.environ.pop("SHIORI_BIND_PORT", None)
        llamacpp_manager.stop_managed_server()


if __name__ == "__main__":
    main()
