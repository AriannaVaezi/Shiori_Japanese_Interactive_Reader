"""Local llama.cpp (`llama-server`) discovery, model resolution, and lifecycle manager."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO
from urllib.parse import urlparse

import httpx

from app import config


def default_models_dirs() -> list[Path]:
    """Return ordered candidate directories where local .gguf models may live."""
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / "Shiori" / "models")
        candidates.extend(
            [
                Path.home() / "AppData" / "Local" / "Shiori" / "models",
                Path.home() / "models",
                Path.home() / ".cache" / "llama.cpp",
            ]
        )
    else:
        candidates.extend(
            [
                Path.home() / ".local" / "share" / "shiori" / "models",
                Path.home() / "models",
                Path.home() / ".cache" / "llama.cpp",
            ]
        )

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        norm = os.path.normcase(os.path.abspath(str(p.expanduser())))
        if norm not in seen:
            seen.add(norm)
            deduped.append(p.expanduser())
    return deduped


def find_llama_server_binary() -> Path | None:
    """Locate `llama-server` on PATH or in standard WinGet install locations."""
    for name in ("llama-server", "llama-server.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    if sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        local_root = Path(local_appdata) if local_appdata else (Path.home() / "AppData" / "Local")
        winget_link = local_root / "Microsoft" / "WinGet" / "Links" / "llama-server.exe"
        if winget_link.is_file():
            return winget_link

        packages_dir = local_root / "Microsoft" / "WinGet" / "Packages"
        if packages_dir.is_dir():
            for pkg_dir in sorted(packages_dir.glob("ggml.llamacpp*")):
                for match in sorted(pkg_dir.rglob("llama-server.exe")):
                    if match.is_file():
                        return match
    return None


def resolve_model_path(
    configured_path: str = "",
    extra_dirs: list[Path] | None = None,
) -> Path | None:
    """Resolve an explicit .gguf file path or pick the first valid .gguf from standard dirs."""
    clean = configured_path.strip()
    if clean:
        candidate = Path(clean).expanduser()
        if candidate.is_file():
            return candidate

    search_dirs = extra_dirs if extra_dirs is not None else default_models_dirs()
    for folder in search_dirs:
        if not folder.is_dir():
            continue
        for item in sorted(folder.glob("*.gguf"), key=lambda p: p.name.lower()):
            if not item.is_file():
                continue
            if any(sfx.lower() in {".part", ".tmp"} for sfx in item.suffixes):
                continue
            return item
    return None


def parse_local_host_port(url: str) -> tuple[str, int] | None:
    """Return `(host, port)` if `url` points to localhost loopback, otherwise `None`."""
    raw = url.strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return ("127.0.0.1" if host == "localhost" else host, port or 8080)


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check whether something is already accepting TCP connections at `(host, port)`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_server_ready(base_url: str, api_key: str = "", timeout: float = 1.5) -> bool:
    """Return True when llama-server reports ready (`200`) on `/health` or `/v1/models`."""
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    headers: dict[str, str] = {}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    try:
        resp = httpx.get(f"{base}/health", headers=headers, timeout=timeout)
        if resp.status_code == 404:
            resp = httpx.get(f"{base}/v1/models", headers=headers, timeout=timeout)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _read_log_tail(log_path: Path, lines: int = 10) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text.strip().splitlines()[-lines:]
    return "\n".join(tail)


class ManagedLlamaServer:
    """Launches and stops a local `llama-server` subprocess paired with `run.py`."""

    def __init__(self, settings: dict[str, str], log_path: Path | None = None) -> None:
        self.settings = dict(settings)
        self.log_path = log_path or (config.DATA_DIR / "llama-server.log")
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_file: IO[bytes] | None = None

    def start_if_needed(self, wait_timeout: float = 45.0) -> bool:
        """Start `llama-server` when configured for local auto-start and not yet running."""
        provider = self.settings.get("llm_provider", "llamacpp").strip().lower()
        if provider != "llamacpp":
            return False

        autostart = self.settings.get("llamacpp_autostart", "true").strip().lower()
        if autostart in {"false", "0", "no", "off"}:
            return False

        url = (self.settings.get("llamacpp_url") or "http://127.0.0.1:8080").strip()
        host_port = parse_local_host_port(url)
        if host_port is None:
            return False
        host, port = host_port

        if is_port_open(host, port):
            print(f"[llama.cpp] Server already listening at {url}")
            return False

        binary = find_llama_server_binary()
        if binary is None:
            print(
                "[llama.cpp] `llama-server` not found. Install via "
                "`winget install --id ggml.llamacpp --exact` or disable auto-start in Settings."
            )
            return False

        model_path = resolve_model_path(self.settings.get("llamacpp_model_path", ""))
        if model_path is None:
            hint_dir = default_models_dirs()[0]
            print(
                f"[llama.cpp] No .gguf model found. Put one in {hint_dir} "
                "or set the GGUF path in Settings."
            )
            return False

        alias = self.settings.get("llamacpp_model", "").strip() or model_path.stem
        api_key = self.settings.get("llamacpp_api_key", "").strip()

        cmd = [
            str(binary),
            "-m",
            str(model_path),
            "--host",
            host,
            "--port",
            str(port),
            "--alias",
            alias,
            "-c",
            "8192",
            "-ngl",
            "99",
            "--jinja",
        ]
        if api_key:
            cmd.extend(["--api-key", api_key])

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_path.open("wb")
        try:
            self.proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)
        except OSError as exc:
            log_handle.close()
            print(f"[llama.cpp] Failed to launch llama-server: {exc}")
            return False
        self._log_file = log_handle

        print(f"[llama.cpp] Starting llama-server ({alias}) at {url}...")
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                exit_code = self.proc.returncode
                self.stop()
                tail = _read_log_tail(self.log_path, lines=10)
                print(f"[llama.cpp] llama-server exited early (code {exit_code}).")
                if tail:
                    print(tail)
                return False
            if is_server_ready(url, api_key=api_key):
                print(f"[llama.cpp] Ready at {url} (model: {alias})")
                return True
            time.sleep(0.5)

        print(
            f"[llama.cpp] Still loading in background (see {self.log_path}); starting Shiori..."
        )
        return True

    def stop(self) -> None:
        """Terminate the managed `llama-server` subprocess and close its log file."""
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=3)
                    except OSError:
                        pass
                except OSError:
                    pass
            self.proc = None

        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
