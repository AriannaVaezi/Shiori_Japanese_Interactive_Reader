"""Local llama.cpp (`llama-server`) discovery, model resolution, and lifecycle manager."""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any
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
    """Locate `llama-server`, preferring the dedicated CUDA build on Windows."""
    if sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        local_roots = []
        if local_appdata:
            local_roots.append(Path(local_appdata))
        home_local = Path.home() / "AppData" / "Local"
        if not local_appdata or Path(local_appdata) != home_local:
            local_roots.append(home_local)
        for root in local_roots:
            cuda_exe = root / "Shiori" / "bin" / "llama-cuda" / "llama-server.exe"
            if cuda_exe.is_file():
                return cuda_exe

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


def _parse_autostart_target(settings: dict[str, str]) -> tuple[str, str, int] | None:
    """Return `(url, host, port)` when settings permit local llama.cpp auto-start."""
    if settings.get("llm_provider", "llamacpp").strip().lower() != "llamacpp":
        return None
    if settings.get("llamacpp_autostart", "true").strip().lower() in {"false", "0", "no", "off"}:
        return None
    url = (settings.get("llamacpp_url") or "http://127.0.0.1:8080").strip()
    host_port = parse_local_host_port(url)
    if host_port is None:
        return None
    return url, host_port[0], host_port[1]


class ManagedLlamaServer:
    """Launches and stops a local `llama-server` subprocess paired with `run.py`."""

    def __init__(self, settings: dict[str, str], log_path: Path | None = None) -> None:
        self.settings = dict(settings)
        self.log_path = log_path or (config.DATA_DIR / "llama-server.log")
        self.proc: subprocess.Popen[bytes] | None = None
        self._log_file: IO[bytes] | None = None
        self._target_url: str = ""
        self._api_key: str = ""
        self._alias: str = ""
        self.last_error: str = ""

    def is_running(self) -> bool:
        """Return True if the managed subprocess is currently alive."""
        return self.proc is not None and self.proc.poll() is None

    def launch(self) -> tuple[bool, str]:
        """Spawn `llama-server` without blocking on model load."""
        self.last_error = ""
        target = _parse_autostart_target(self.settings)
        if target is None:
            return False, ""
        url, host, port = target

        if is_port_open(host, port):
            print(f"[llama.cpp] Server already listening at {url}")
            return False, ""

        binary = find_llama_server_binary()
        if binary is None:
            msg = (
                "`llama-server` not found. Install via "
                "`winget install --id ggml.llamacpp --exact` or disable auto-start in Settings."
            )
            print(f"[llama.cpp] {msg}")
            self.last_error = msg
            return False, msg

        model_path = resolve_model_path(self.settings.get("llamacpp_model_path", ""))
        if model_path is None:
            hint_dir = default_models_dirs()[0]
            msg = (
                f"No .gguf model found. Put one in {hint_dir} "
                "or set the GGUF path in Settings."
            )
            print(f"[llama.cpp] {msg}")
            self.last_error = msg
            return False, msg

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
            "4096",
            "-np",
            "1",
            "-ngl",
            "99",
            "--jinja",
        ]
        if api_key:
            cmd.extend(["--api-key", api_key])

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_path.open("wb")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log_handle.close()
            msg = f"Failed to launch llama-server: {exc}"
            print(f"[llama.cpp] {msg}")
            self.last_error = msg
            return False, msg

        self._log_file = log_handle
        self._target_url = url
        self._api_key = api_key
        self._alias = alias
        print(f"[llama.cpp] Starting llama-server ({alias}) at {url}...")
        return True, ""

    def wait_until_ready(self, wait_timeout: float = 45.0) -> bool:
        """Poll the launched subprocess until `/health` or `/v1/models` succeeds or it exits."""
        if self.proc is None or not self._target_url:
            return False
        deadline = time.monotonic() + max(0.0, wait_timeout)
        while True:
            if self.proc is None:
                return False
            if self.proc.poll() is not None:
                exit_code = self.proc.returncode
                self.stop()
                tail = _read_log_tail(self.log_path, lines=10)
                msg = f"llama-server exited early (code {exit_code})."
                self.last_error = msg
                print(f"[llama.cpp] {msg}")
                if tail:
                    print(tail)
                return False
            if is_server_ready(self._target_url, api_key=self._api_key):
                self.last_error = ""
                print(f"[llama.cpp] Ready at {self._target_url} (model: {self._alias})")
                return True
            if time.monotonic() >= deadline:
                print(
                    f"[llama.cpp] Still loading in background (see {self.log_path}); starting Shiori..."
                )
                return False
            time.sleep(0.5)

    def start_if_needed(self, wait_timeout: float = 45.0) -> bool:
        """Start `llama-server` when configured for local auto-start and not yet running."""
        launched, _ = self.launch()
        if not launched:
            return False
        if wait_timeout <= 0:
            return True
        ready = self.wait_until_ready(wait_timeout=wait_timeout)
        return ready or self.is_running()

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


_MANAGER_LOCK = threading.Lock()
_MANAGED_SERVER: ManagedLlamaServer | None = None
_MANAGED_SIGNATURE: tuple[str, int, str, str, str] | None = None
_MANAGED_MODEL: str = ""
_STARTING: bool = False
_LAST_ERROR: str = ""
_START_THREAD: threading.Thread | None = None
_ATEXIT_REGISTERED = False


def _watch_readiness(mgr: ManagedLlamaServer, wait_timeout: float = 90.0) -> None:
    global _STARTING, _LAST_ERROR
    try:
        mgr.wait_until_ready(wait_timeout=max(1.0, wait_timeout))
    finally:
        with _MANAGER_LOCK:
            if _MANAGED_SERVER is mgr:
                _STARTING = False
                if mgr.last_error:
                    _LAST_ERROR = mgr.last_error


def _tests_block_autostart(allow_in_tests: bool) -> bool:
    return not allow_in_tests and bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("SHIORI_DISABLE_LLAMACPP_AUTOSTART") == "1"
    )


def ensure_managed_server_running(
    settings: dict[str, str],
    wait_timeout: float = 45.0,
    *,
    allow_in_tests: bool = False,
) -> bool:
    """Start or reuse a managed local `llama-server` matching `settings`."""
    global _MANAGED_SERVER, _MANAGED_SIGNATURE, _MANAGED_MODEL, _STARTING, _LAST_ERROR, _ATEXIT_REGISTERED

    if _tests_block_autostart(allow_in_tests):
        return False

    target = _parse_autostart_target(settings)
    if target is None:
        return False
    url, host, port = target

    model_path = resolve_model_path(settings.get("llamacpp_model_path", ""))
    if model_path is None:
        with _MANAGER_LOCK:
            _STARTING = False
            _LAST_ERROR = f"No .gguf model found in {default_models_dirs()[0]}"
        return False

    alias = settings.get("llamacpp_model", "").strip() or model_path.stem
    api_key = settings.get("llamacpp_api_key", "").strip()
    signature = (host, port, str(model_path), alias, api_key)

    mgr: ManagedLlamaServer | None = None
    with _MANAGER_LOCK:
        if _MANAGED_SERVER is not None and _MANAGED_SERVER.is_running():
            if _MANAGED_SIGNATURE == signature:
                mgr = _MANAGED_SERVER
            else:
                _MANAGED_SERVER.stop()
                _MANAGED_SERVER = None
                _MANAGED_SIGNATURE = None
                _MANAGED_MODEL = ""

        if mgr is None:
            if is_port_open(host, port):
                _STARTING = False
                _LAST_ERROR = ""
                return True

            candidate = ManagedLlamaServer(settings)
            launched, err = candidate.launch()
            if not launched:
                if is_port_open(host, port):
                    _STARTING = False
                    _LAST_ERROR = ""
                    return True
                _STARTING = False
                if err:
                    _LAST_ERROR = err
                return False

            _MANAGED_SERVER = candidate
            _MANAGED_SIGNATURE = signature
            _MANAGED_MODEL = alias
            _STARTING = True
            _LAST_ERROR = ""
            if not _ATEXIT_REGISTERED:
                atexit.register(stop_managed_server)
                _ATEXIT_REGISTERED = True
            mgr = candidate

    if is_server_ready(url, api_key=api_key, timeout=0.6):
        with _MANAGER_LOCK:
            if _MANAGED_SERVER is mgr:
                _STARTING = False
                _LAST_ERROR = ""
        return True

    if wait_timeout <= 0:
        threading.Thread(
            target=_watch_readiness,
            args=(mgr, 90.0),
            daemon=True,
        ).start()
        return mgr.is_running()

    ready = mgr.wait_until_ready(wait_timeout=wait_timeout)
    with _MANAGER_LOCK:
        if _MANAGED_SERVER is mgr:
            _STARTING = False
            if mgr.last_error:
                _LAST_ERROR = mgr.last_error
    return ready or mgr.is_running()


def _run_async_start(
    settings: dict[str, str],
    wait_timeout: float,
    allow_in_tests: bool,
) -> None:
    global _START_THREAD, _STARTING
    try:
        ensure_managed_server_running(
            settings,
            wait_timeout=wait_timeout,
            allow_in_tests=allow_in_tests,
        )
    finally:
        with _MANAGER_LOCK:
            _START_THREAD = None
            if _MANAGED_SERVER is None or not _MANAGED_SERVER.is_running():
                _STARTING = False


def start_managed_server_async(
    settings: dict[str, str],
    wait_timeout: float = 90.0,
    *,
    allow_in_tests: bool = False,
) -> bool:
    """Kick off managed `llama-server` startup on a background daemon thread."""
    global _START_THREAD, _STARTING, _LAST_ERROR

    if _tests_block_autostart(allow_in_tests):
        return False

    if _parse_autostart_target(settings) is None:
        return False

    snapshot = dict(settings)
    with _MANAGER_LOCK:
        if _START_THREAD is not None and _START_THREAD.is_alive():
            return True
        if _MANAGED_SERVER is not None and _MANAGED_SERVER.is_running():
            return True
        _STARTING = True
        _LAST_ERROR = ""
        t = threading.Thread(
            target=_run_async_start,
            args=(snapshot, wait_timeout, allow_in_tests),
            daemon=True,
        )
        _START_THREAD = t
        t.start()
        return True


def stop_managed_server() -> None:
    """Stop any managed local `llama-server` started by this process."""
    global _MANAGED_SERVER, _MANAGED_SIGNATURE, _MANAGED_MODEL, _STARTING
    with _MANAGER_LOCK:
        if _MANAGED_SERVER is not None:
            _MANAGED_SERVER.stop()
            _MANAGED_SERVER = None
            _MANAGED_SIGNATURE = None
            _MANAGED_MODEL = ""
        _STARTING = False


def get_status(settings: dict[str, str]) -> dict[str, Any]:
    """Return non-blocking status metadata for the currently configured LLM provider."""
    provider = (settings.get("llm_provider") or "llamacpp").strip().lower()
    if provider == "llamacpp":
        url = (settings.get("llamacpp_url") or "http://127.0.0.1:8080").strip()
        api_key = (settings.get("llamacpp_api_key") or "").strip()
        with _MANAGER_LOCK:
            managed = _MANAGED_SERVER is not None and _MANAGED_SERVER.is_running()
            starting = _STARTING
            last_error = _LAST_ERROR
            managed_model = _MANAGED_MODEL
        model = (settings.get("llamacpp_model") or "").strip() or managed_model

        if is_server_ready(url, api_key=api_key, timeout=0.6):
            return {
                "provider": "llamacpp",
                "status": "ready",
                "model": model or "local model",
                "detail": f"Connected at {url}",
                "managed": managed,
            }
        if starting or managed:
            return {
                "provider": "llamacpp",
                "status": "loading",
                "model": model,
                "detail": "Loading local llama.cpp model in background…",
                "managed": True,
            }
        if last_error:
            return {
                "provider": "llamacpp",
                "status": "error",
                "model": model,
                "detail": last_error,
                "managed": False,
            }
        return {
            "provider": "llamacpp",
            "status": "stopped",
            "model": model,
            "detail": f"llama.cpp not reachable at {url}",
            "managed": False,
        }

    if provider == "ollama":
        url = (settings.get("ollama_url") or "http://localhost:11434").strip()
        model = (settings.get("ollama_model") or "").strip() or "auto"
        return {
            "provider": "ollama",
            "status": "external",
            "model": model,
            "detail": f"Using Ollama at {url}",
            "managed": False,
        }

    if provider == "gemini":
        has_key = bool(
            (settings.get("gemini_api_key") or "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip()
        )
        model = (settings.get("gemini_model") or "gemini-2.5-flash").strip()
        return {
            "provider": "gemini",
            "status": "ready" if has_key else "unconfigured",
            "model": model,
            "detail": "Gemini API ready" if has_key else "Add Gemini API key in Settings",
            "managed": False,
        }

    return {
        "provider": provider,
        "status": "stopped",
        "model": "",
        "detail": "",
        "managed": False,
    }
