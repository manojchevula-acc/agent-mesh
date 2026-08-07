"""
scripts/health_check.py
-----------------------
Verify the Hub Server (port 8090) and all MCP servers are reachable AND
pass the full MCP initialize handshake.  A server that responds to TCP/HTTP
but returns HTTP 500 on the MCP init (a common stuck-process symptom) is
reported as FAIL with the real root cause.

The server list is fetched from GET http://localhost:8090/servers.
If the Hub Server is down, falls back to reading mcp-hub.json directly.

Usage:
    python scripts/health_check.py          # check all servers
    python scripts/health_check.py 9100     # check one port only

Exit code: 0 if all servers pass, 1 if any fail.
"""

import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx

# Locate mcp-hub.json relative to this script (../mcp-hub.json)
HUB_FILE = Path(__file__).parent.parent / "mcp-hub.json"


def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        s = socket.socket()
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def check_hub_server() -> tuple[bool, list, dict]:
    """
    GET /health and /servers from the Hub Server (localhost:8090).
    Returns (hub_ok, servers, hub_info).
    Prints [PASS]/[FAIL] lines directly.
    """
    url_health  = "http://localhost:8090/health"
    url_servers = "http://localhost:8090/servers"

    # ── Health ────────────────────────────────────────────────────────────────
    try:
        r = httpx.get(url_health, timeout=5)
        if r.status_code == 200 and r.json().get("status") == "ok":
            print(f"  [PASS]  Hub Server                          {url_health}")
            hub_info = r.json()
        else:
            print(f"  [FAIL]  Hub Server                          {url_health} — HTTP {r.status_code}")
            return False, [], {}
    except Exception as exc:
        print(f"  [FAIL]  Hub Server                          {url_health} — {exc}")
        return False, [], {}

    # ── Servers list ──────────────────────────────────────────────────────────
    servers: list = []
    try:
        r2 = httpx.get(url_servers, timeout=5)
        if r2.status_code == 200:
            servers = r2.json().get("servers", [])
            print(f"         {len(servers)} servers registered in Hub Server")
    except Exception as exc:
        print(f"  [WARN]  Could not fetch {url_servers} — {exc}")

    return True, servers, hub_info


async def check_mcp_server(server: dict) -> tuple[bool, str]:
    """Full MCP handshake test — returns (ok, human-readable message)."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client
    from urllib.parse import urlparse

    sid       = server.get("id", "unknown")
    endpoint  = server.get("endpoint", "")
    transport = server.get("transport", "sse")

    # ── TCP check ────────────────────────────────────────────────────────────
    try:
        parsed = urlparse(endpoint)
        host   = parsed.hostname or "127.0.0.1"
        port   = parsed.port or 80
    except Exception:
        return False, f"cannot parse endpoint URL: {endpoint}"

    if not tcp_reachable(host, port):
        return False, f"port {port} not reachable (server not started)"

    # ── MCP handshake ────────────────────────────────────────────────────────
    try:
        if transport == "sse":
            async with sse_client(endpoint) as (r, w):
                async with ClientSession(r, w) as session:
                    await asyncio.wait_for(session.initialize(), timeout=8.0)
                    tools = await asyncio.wait_for(session.list_tools(), timeout=8.0)
                    n = len(tools.tools)
        elif transport in ("streamable-http", "http"):
            async with streamablehttp_client(endpoint) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await asyncio.wait_for(session.initialize(), timeout=8.0)
                    tools = await asyncio.wait_for(session.list_tools(), timeout=8.0)
                    n = len(tools.tools)
        else:
            return False, f"unsupported transport {transport!r}"

        return True, f"{n} tools OK"

    except BaseException as exc:
        # Unwrap anyio/asyncio BaseExceptionGroup to the real root cause
        root = exc
        if hasattr(exc, "exceptions") and exc.exceptions:
            root = exc.exceptions[0]
        if hasattr(root, "exceptions") and root.exceptions:
            root = root.exceptions[0]

        msg = str(root)
        hint = ""
        if "500" in msg:
            hint = " — server process is stuck; kill and restart it"
        elif "ConnectionRefused" in type(root).__name__ or "refused" in msg.lower():
            hint = " — port is open but connection was refused; check server logs"

        return False, f"MCP handshake failed ({type(root).__name__}: {msg[:80]}){hint}"


async def main(filter_port: int | None = None):
    print()

    # ── Hub Server check ─────────────────────────────────────────────────────
    hub_ok, servers, hub_info = check_hub_server()
    print()

    if not hub_ok:
        print("WARNING: Hub Server is down — falling back to mcp-hub.json")
        if not HUB_FILE.exists():
            print(f"ERROR: {HUB_FILE} not found")
            sys.exit(1)
        hub      = json.loads(HUB_FILE.read_text(encoding="utf-8"))
        servers  = hub.get("servers", [])
        hub_name = hub.get("hub_name", "?")
        version  = hub.get("version",  "?")
        print()
    else:
        hub_name = hub_info.get("hub_name", "?")
        version  = hub_info.get("version",  "?")

    if filter_port:
        from urllib.parse import urlparse
        servers = [s for s in servers
                   if urlparse(s.get("endpoint", "")).port == filter_port]
        if not servers:
            print(f"No server found with port {filter_port}")
            sys.exit(1)

    print(f"Health Check -- {hub_name} v{version}")
    print("=" * 72)

    tasks   = [asyncio.create_task(check_mcp_server(s)) for s in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    passed = 0
    needs_restart = []

    for server, result in zip(servers, results):
        sid = server.get("id", "?")
        ep  = server.get("endpoint", "")
        if isinstance(result, BaseException):
            ok, detail = False, f"unexpected error: {result}"
        else:
            ok, detail = result

        status = "OK  " if ok else "FAIL"
        print(f"  [{status}]  {sid:<32}  {ep}")
        if not ok:
            print(f"           {detail}")
        else:
            passed += 1

        if not ok and "stuck" in detail:
            needs_restart.append(server)

    print("=" * 72)
    total = len(servers)
    print(f"Status: {passed}/{total} servers healthy")

    any_failure = (passed < total) or (not hub_ok)

    if any_failure:
        print()
        print("Fix — restart all servers:")
        print("  bash scripts/start_servers.sh      (Git Bash)")
        print("  .\\scripts\\start_servers.ps1         (PowerShell)")
        print()
        print("Fix — if Hub Server is down:")
        print("  python hub_server.py")
        print("  (or ensure start_servers.sh|ps1 was run)")
        print()
        print("Fix — restart one server (example for port 9100):")
        print("  # Git Bash: kill by port then restart")
        print("  pid=$(netstat -ano 2>/dev/null | awk '/0.0.0.0:9100 /{print $NF}' | head -1)")
        print("  taskkill //PID $pid //F")
        print("  cd datalayer-as-service")
        print("  MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.customer_server &")
        print()
        print("See RUNBOOK.md → 'Troubleshooting' for full details.")
        sys.exit(1)

    print()


if __name__ == "__main__":
    port_filter = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(port_filter))
