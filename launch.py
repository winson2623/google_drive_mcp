"""Launcher for the google-drive MCP server (Python standard library only).


First run: build a private virtual environment in the plugin's data directory
and `pip install -r requirements.txt` into it (pinned versions). Later runs:
start the server straight away.


Claude Code gives an MCP server about 30 seconds to answer its handshake, but a
cold dependency install takes 1-3 minutes. So while installing, this launcher
answers the MCP handshake itself (tools report "still setting up"), and once the
install finishes it starts the real server, replays the handshake to it, tells
Claude Code that the tool list changed, and relays stdio from then on. The user
never has to reconnect or run any command.


Nothing is installed system-wide, nothing is downloaded and executed as a
script, and no shell execution policy is touched.


stdout is the MCP channel: every message from this file goes to stderr.
"""


from __future__ import annotations


import collections
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or ROOT / ".plugin-data")
VENV_DIR = DATA / "venv"
PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = DATA / "requirements.sha256"
SERVER_CMD = [str(PY), "-m", "google_drive_mcp.server"]
MIN_PYTHON = (3, 10)
HANDSHAKE_TIMEOUT = 30


PLACEHOLDER_TOOLS = [
    {
        "name": name,
        "description": f"{blurb} (The plugin is still finishing its one-time setup; "
        "calling this reports progress.)",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    }
    for name, blurb in (
        ("authorize", "Sign in to Google Drive (read-only)."),
        ("list_files", "List files in the Google Drive folder."),
        ("read_file", "Read a file from Google Drive."),
    )
]




def log(message: str) -> None:
    print(f"[google-drive] {message}", file=sys.stderr, flush=True)




# --------------------------------------------------------------------------
# Environment + install
# --------------------------------------------------------------------------


def _digest() -> str:
    h = hashlib.sha256(REQUIREMENTS.read_bytes())
    h.update(f"py{sys.version_info.major}.{sys.version_info.minor}".encode())
    return h.hexdigest()




def is_ready() -> bool:
    try:
        return PY.exists() and STAMP.read_text(encoding="utf-8").strip() == _digest()
    except OSError:
        return False




def server_env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"  # ignore packages outside the private venv
    env.setdefault("GOOGLE_CREDENTIALS_FILE", str(ROOT / "credentials.json"))
    env.setdefault("GOOGLE_TOKEN_FILE", str(DATA / "token.json"))
    return env




class InstallError(Exception):
    pass




def install() -> None:
    """Create the venv if needed and install the pinned requirements."""
    DATA.mkdir(parents=True, exist_ok=True)
    if not PY.exists():
        log("creating private virtual environment...")
        # stdin=DEVNULL on every child: on Windows a child that inherits the
        # pipe our stdin-reader thread is blocked on can stall at startup.
        made = subprocess.run(
            [sys.executable, "-m", "venv", "--clear", str(VENV_DIR)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        if made.returncode != 0:
            raise InstallError(
                "could not create a virtual environment (on Debian/Ubuntu run "
                "`sudo apt install python3-venv` first):\n" + made.stderr[-800:]
            )


    log("installing pinned dependencies (one-time, can take a few minutes)...")
    tail: collections.deque[str] = collections.deque(maxlen=12)
    proc = subprocess.Popen(
        [
            str(PY), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
            "-r", str(REQUIREMENTS),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        print(line, file=sys.stderr, flush=True)
    if proc.wait() != 0:
        raise InstallError("pip install failed:\n" + "\n".join(tail))


    check = subprocess.run(
        [str(PY), "-c", "import google_drive_mcp.server"],
        env=server_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise InstallError("installed, but the server cannot start:\n" + check.stderr[-800:])
    STAMP.write_text(_digest(), encoding="utf-8")
    log("dependencies installed.")




# --------------------------------------------------------------------------
# Minimal MCP stdio plumbing (newline-delimited JSON-RPC)
# --------------------------------------------------------------------------


_out_lock = threading.Lock()




def send(msg: dict) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
    with _out_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()




def _reply(req: dict, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": req["id"], "result": result})




class Setup:
    """Install progress, shared between the installer thread and the stub."""


    def __init__(self) -> None:
        self.started = time.monotonic()
        self.done = threading.Event()
        self.error: str | None = None
        threading.Thread(target=self._run, name="installer", daemon=True).start()


    def _run(self) -> None:
        try:
            install()
        except InstallError as exc:
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.done.set()


    def message(self) -> str:
        if self.error:
            return (
                "Google Drive Reader setup FAILED. Tell the user to check their "
                "internet connection and that Python is 3.10 or newer, then run /mcp "
                "and reconnect 'google-drive' to retry. Details:\n" + self.error
            )
        secs = int(time.monotonic() - self.started)
        return (
            f"Google Drive Reader is still finishing its one-time setup "
            f"(installing Python dependencies, running for {secs}s; usually 1-3 "
            "minutes). Nothing is needed from the user: tell them to wait a moment "
            "and then try again."
        )




def serve_stub(setup: Setup, inbox: "queue.Queue[bytes | None]") -> dict | None:
    """Answer the handshake while installing. Returns client init params when
    the install has finished and we should hand off, or None if stdin closed."""
    init_params: dict | None = None
    while True:
        if setup.done.is_set() and not setup.error and init_params is not None:
            return init_params
        try:
            line = inbox.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, has_id = msg.get("method"), "id" in msg
        if not has_id or not method:
            continue  # notifications and stray responses
        if method == "initialize":
            init_params = msg.get("params") or {}
            _reply(msg, {
                "protocolVersion": init_params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "google-drive", "version": "bootstrap"},
            })
        elif method == "ping":
            _reply(msg, {})
        elif method == "tools/list":
            _reply(msg, {"tools": PLACEHOLDER_TOOLS})
        elif method == "tools/call":
            _reply(msg, {
                "content": [{"type": "text", "text": setup.message()}],
                "isError": True,
            })
        elif method in ("resources/list", "prompts/list", "resources/templates/list"):
            _reply(msg, {method.split("/")[0]: []})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "error": {"code": -32601, "message": f"Method not found: {method}"}})




def hand_off(init_params: dict, inbox: "queue.Queue[bytes | None]") -> int:
    """Start the real server, replay the handshake to it, then relay stdio."""
    child = subprocess.Popen(SERVER_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             env=server_env())
    assert child.stdin is not None and child.stdout is not None
    handshake_done = threading.Event()


    def pump_child_output() -> None:
        for line in child.stdout:  # type: ignore[union-attr]
            if not handshake_done.is_set():
                try:
                    if json.loads(line).get("id") == "bootstrap-init":
                        handshake_done.set()
                        continue
                except ValueError:
                    pass
            with _out_lock:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()


    threading.Thread(target=pump_child_output, daemon=True).start()


    def to_child(msg: dict) -> None:
        child.stdin.write(json.dumps(msg, separators=(",", ":")).encode() + b"\n")  # type: ignore[union-attr]
        child.stdin.flush()  # type: ignore[union-attr]


    to_child({"jsonrpc": "2.0", "id": "bootstrap-init", "method": "initialize",
              "params": init_params})
    if not handshake_done.wait(HANDSHAKE_TIMEOUT):
        log("server did not answer the handshake; giving up")
        child.kill()
        return 1
    to_child({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    log("setup complete; real server is live.")


    while True:
        code = child.poll()
        if code is not None:
            return code
        try:
            line = inbox.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:  # client closed stdin: shut the server down
            try:
                child.stdin.close()
                return child.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                child.kill()
                return 0
        try:
            child.stdin.write(line)
            child.stdin.flush()
        except OSError:
            return child.wait()




# --------------------------------------------------------------------------


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        log(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, found "
            f"{sys.version_info.major}.{sys.version_info.minor}.")
        return 1


    if is_ready():  # fast path: nothing to install, become the server
        if os.name == "nt":
            return subprocess.run(SERVER_CMD, env=server_env()).returncode
        os.execve(SERVER_CMD[0], SERVER_CMD, server_env())


    inbox: "queue.Queue[bytes | None]" = queue.Queue()


    def read_stdin() -> None:
        for line in sys.stdin.buffer:
            inbox.put(line)
        inbox.put(None)


    threading.Thread(target=read_stdin, daemon=True).start()


    setup = Setup()
    init_params = serve_stub(setup, inbox)
    if init_params is None:
        return 0
    return hand_off(init_params, inbox)




if __name__ == "__main__":
    sys.exit(main())


