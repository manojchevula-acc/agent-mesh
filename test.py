#!/usr/bin/env python3
"""
launch_all.py — one-command launcher for the agent-mesh stack.

Opens each service in its OWN terminal window so you can watch its logs live,
and starts them in the right order (MCP servers first, then the mesh that
connects to them, then the REST bridge, then the React UI).

Usage:
    python launch_all.py

Place this file in the ROOT folder that contains:
    datalayer-as-service/
    rag-as-a-service/
    agent-mesh/

Stop everything by closing the individual windows (Ctrl+C in each), or run:
    python launch_all.py --stop        (best-effort: closes windows by title)
"""

import os
import sys
import time
import shutil
import platform
import subprocess
from pathlib import Path

# --------------------------------------------------------------------------
# Config: each service = (window title, working dir, command, env overrides, wait_after)
# `wait_after` = seconds to pause AFTER launching, before starting the next one.
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
IS_WINDOWS = platform.system() == "Windows"

# Use the same Python interpreter that's running this script.
PY = sys.executable or "python"

SERVICES = [
    {
        "title": "DataLayer-MCP",
        "cwd": ROOT / "datalayer-as-service",
        "cmd": [PY, "-m", "mcp_server.server"],
        "env": {"MCP_TRANSPORT": "http", "MCP_HOST": "127.0.0.1", "MCP_PORT": "9100"},
        "wait_after": 4,   # give the MCP server time to bind its port
    },
    {
        "title": "RAG-MCP",
        "cwd": ROOT / "rag-as-a-service",
        "cmd": [PY, "-m", "mcp_integration.server"],
        "env": {"MCP_TRANSPORT": "http", "MCP_HOST": "127.0.0.1", "MCP_PORT": "9000"},
        "wait_after": 4,
    },
    {
        "title": "Agent-Mesh",
        "cwd": ROOT / "agent-mesh",
        "cmd": [PY, "launch_mesh.py"],
        "env": {},
        "wait_after": 5,   # let the 4 A2A nodes register before the REST bridge
    },
    {
        "title": "REST-API",
        "cwd": ROOT / "agent-mesh",
        "cmd": [PY, "api_server.py"],
        "env": {},
        "wait_after": 3,
    },
    {
        "title": "React-UI",
        "cwd": ROOT / "agent-mesh" / "frontend",
        "cmd": ["npm", "run", "dev"],
        "env": {},
        "wait_after": 0,
    },
]


def _preflight():
    """Verify folders and key files exist before launching anything."""
    problems = []
    for svc in SERVICES:
        if not svc["cwd"].is_dir():
            problems.append(f"  - Missing folder: {svc['cwd']}")
    if not IS_WINDOWS and shutil.which("npm") is None:
        problems.append("  - 'npm' not found on PATH (needed for React UI)")
    if problems:
        print("Pre-flight check failed:\n" + "\n".join(problems))
        print("\nRun this script from the folder that contains the service directories.")
        sys.exit(1)


def _launch_windows(svc):
    """Open a new PowerShell window, cd into cwd, set env vars, run the command."""
    env_prefix = "".join(f'$env:{k}="{v}"; ' for k, v in svc["env"].items())
    cmd_str = subprocess.list2cmdline(svc["cmd"])
    inner = f'{env_prefix}{cmd_str}'
    # -NoExit keeps the window open so you can read logs / errors.
    ps_args = [
        "powershell", "-NoExit", "-Command",
        f'$host.UI.RawUI.WindowTitle = "{svc["title"]}"; '
        f'Set-Location -Path "{svc["cwd"]}"; {inner}',
    ]
    subprocess.Popen(["cmd", "/c", "start", svc["title"], *ps_args])


def _launch_unix(svc):
    """Open a new terminal on macOS/Linux."""
    env_prefix = " ".join(f'{k}="{v}"' for k, v in svc["env"].items())
    cmd_str = " ".join(subprocess.list2cmdline([c]) for c in svc["cmd"])
    full = f'cd "{svc["cwd"]}" && {env_prefix} {cmd_str}'.strip()

    if platform.system() == "Darwin":
        # macOS Terminal via AppleScript
        script = f'tell application "Terminal" to do script "{full}"'
        subprocess.Popen(["osascript", "-e", script])
    else:
        # Linux: try a few common terminals
        for term in (["gnome-terminal", "--", "bash", "-c", f"{full}; exec bash"],
                     ["konsole", "-e", "bash", "-c", f"{full}; exec bash"],
                     ["xterm", "-e", f"{full}; exec bash"]):
            if shutil.which(term[0]):
                subprocess.Popen(term)
                return
        print(f"  ! No supported terminal found; run manually:\n    {full}")


def main():
    if "--stop" in sys.argv and IS_WINDOWS:
        for svc in SERVICES:
            subprocess.run(["taskkill", "/FI", f'WINDOWTITLE eq {svc["title"]}*', "/F"],
                           capture_output=True)
        print("Sent stop signal to service windows.")
        return

    _preflight()
    print(f"Launching {len(SERVICES)} services from: {ROOT}\n")

    for svc in SERVICES:
        print(f"  -> {svc['title']:14} ({svc['cwd'].name})")
        if IS_WINDOWS:
            _launch_windows(svc)
        else:
            _launch_unix(svc)
        if svc["wait_after"]:
            time.sleep(svc["wait_after"])

    print("\nAll services launched in separate windows.")
    print("Watch each window for logs. Close a window (or Ctrl+C in it) to stop that service.")
    if IS_WINDOWS:
        print("To stop all at once:  python launch_all.py --stop")


if __name__ == "__main__":
    main()