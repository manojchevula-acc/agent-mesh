#!/usr/bin/env bash
# Start all MCP servers for the FAB MCP Hub.
# Starts: Weather (8001), Calculator (8002), Data (8003),
#         FAB Customer Intelligence (9100), FAB Pricing Engine (9200),
#         Hub Server (8090), Chat UI (8080)
#
# Usage (Git Bash / Linux / macOS):
#   bash scripts/start_servers.sh
#   chmod +x scripts/start_servers.sh && ./scripts/start_servers.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

# ---------------------------------------------------------------------------
# Resolve Python — prefer local .venv, then sibling venv, then system
# ---------------------------------------------------------------------------
find_python() {
    local candidates=(
        "$ROOT/.venv/Scripts/python.exe"                      # Windows (Git Bash)
        "$ROOT/.venv/bin/python"                              # Linux / macOS
        "$ROOT/../fab-mcp-hub/.venv/Scripts/python.exe"       # sibling venv (Windows)
        "$ROOT/../fab-mcp-hub/.venv/bin/python"               # sibling venv (Linux/macOS)
    )
    for p in "${candidates[@]}"; do
        if [ -f "$p" ]; then echo "$p"; return; fi
    done
    command -v python3 2>/dev/null || echo "python"
}

PY="$(find_python)"
echo "Using Python: $PY"
echo ""

if ! "$PY" --version &>/dev/null; then
    echo "ERROR: Python not found. Activate the venv first:"
    echo "  source .venv/Scripts/activate"
    exit 1
fi

# ---------------------------------------------------------------------------
# Ensure MySQL is running (Windows: auto-start if not listening on 3306)
# ---------------------------------------------------------------------------
check_mysql() {
    # Try a TCP connect to 3306
    if command -v nc &>/dev/null; then
        nc -z 127.0.0.1 3306 2>/dev/null && return 0
    elif command -v python &>/dev/null; then
        "$PY" -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',3306)); s.close()" 2>/dev/null && return 0
    fi
    return 1
}

MYSQLD_WIN="/c/Program Files/MySQL/MySQL Server 8.4/bin/mysqld.exe"
MYSQL_INI="C:/MySQL/my.ini"

if check_mysql; then
    echo "  MySQL:   already running on port 3306"
elif [ -f "$MYSQLD_WIN" ]; then
    echo "  MySQL:   not running — starting mysqld..."
    "$MYSQLD_WIN" --defaults-file="$MYSQL_INI" &
    # Wait up to 15 s for port 3306
    for i in $(seq 1 15); do
        sleep 1
        if check_mysql; then
            echo "  MySQL:   started (port 3306 ready)"
            break
        fi
        if [ "$i" -eq 15 ]; then
            echo "  MySQL:   WARNING — port 3306 still not open after 15 s"
            echo "           FAB data layer tools may fail. Start MySQL manually:"
            echo "           \"$MYSQLD_WIN\" --defaults-file=\"$MYSQL_INI\" &"
        fi
    done
else
    echo "  MySQL:   WARNING — not running and mysqld not found at default path."
    echo "           FAB data layer tools will return errors until MySQL is started."
fi
echo ""

# ---------------------------------------------------------------------------
# Start servers in the background
# ---------------------------------------------------------------------------
PIDS=()

start_demo() {
    # name script port server_id
    local name="$1" script="$2" port="$3" server_id="${4:-}"
    MCP_SERVER_ID="$server_id" "$PY" "$ROOT/$script" "$port" &
    local pid=$!
    PIDS+=("$pid")
    printf "  %-38s port %-4s  PID %s\n" "$name" "$port" "$pid"
    sleep 1
}

start_datalayer() {
    # Must run from the datalayer-as-service/ dir so Python finds the mcp_server package.
    # module port label server_id
    local module="$1" port="$2" label="$3" server_id="${4:-}"
    (
        cd "$ROOT/datalayer-as-service" || exit 1
        MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT="$port" MCP_SERVER_ID="$server_id" \
            "$PY" -m "$module"
    ) &
    local pid=$!
    PIDS+=("$pid")
    printf "  %-42s port %-4s  PID %s\n" "$label" "$port" "$pid"
    sleep 2   # data layer servers take slightly longer to initialise
}

# ---------------------------------------------------------------------------
# Kill any stale processes on hub ports to avoid "port already in use" errors
# or stuck processes that accept TCP connections but return HTTP 500.
# ---------------------------------------------------------------------------
echo "Clearing ports 8001 8002 8003 8080 8090 9100 9200..."
if command -v taskkill &>/dev/null; then
    for port in 8001 8002 8003 8080 8090 9100 9200; do
        local_pid=$(netstat -ano 2>/dev/null | awk "/[: ]$port / && /LISTEN/{print \$NF}" | head -1)
        [ -n "$local_pid" ] && taskkill //PID "$local_pid" //F &>/dev/null || true
    done
else
    # Linux / macOS
    for port in 8001 8002 8003 8080 8090 9100 9200; do
        fuser -k "${port}/tcp" &>/dev/null || true
    done
fi
sleep 1
echo ""

echo "Starting MCP servers..."
start_demo "Weather Server   (weather_server.py)" "datalayer-as-service/mcp_server/weather_server.py" 8001 "weather-server"
start_demo "Calculator Server (calc_server.py)"    "datalayer-as-service/mcp_server/calc_server.py"    8002 "calculator-server"
start_demo "Data Server       (data_server.py)"    "datalayer-as-service/mcp_server/data_server.py"    8003 "data-server"
start_datalayer "mcp_server.customer_server" 9100 "FAB Customer Intelligence (customer_server.py)" "fab-customer-server"
start_datalayer "mcp_server.pricing_server"  9200 "FAB Pricing Engine        (pricing_server.py)"  "fab-pricing-server"

# Hub Server — REST routing/discovery API on port 8090 (REQUIRED before agent.py)
export HUB_SERVER_URL=http://localhost:8090
HUB_PORT=8090 "$PY" "$ROOT/hub_service/hub_server.py" &
hub_pid=$!
PIDS+=("$hub_pid")
printf "  %-42s port %-4s  PID %s\n" "Hub Server (hub_server.py)" "8090" "$hub_pid"
sleep 2

# Chat UI — runs from project root on port 8080
CHAT_PORT=8080 "$PY" "$ROOT/chat_service/chat_server.py" &
chat_pid=$!
PIDS+=("$chat_pid")
printf "  %-42s port %-4s  PID %s\n" "Chat UI Server (chat_server.py)" "8080" "$chat_pid"
sleep 2

echo ""
echo "All servers started.  Endpoints:"
echo "  Weather:                   http://localhost:8001/sse       [SSE]"
echo "  Calculator:                http://localhost:8002/sse       [SSE]"
echo "  Data Lookup:               http://localhost:8003/sse       [SSE]"
echo "  FAB Customer Intelligence: http://127.0.0.1:9100/mcp/     [streamable-HTTP]"
echo "  FAB Pricing Engine:        http://127.0.0.1:9200/mcp/     [streamable-HTTP]"
echo "  Hub Server:                http://localhost:8090/health    [REST API]"
echo "  Chat UI:                   http://localhost:8080            [Web Browser]"
echo ""
echo "MySQL:        127.0.0.1:3306  test_user / fab_semantic  (already configured)"
echo ""
echo "Open the Chat UI in your browser:"
echo "  http://localhost:8080"
echo ""
echo "CLI agent (new terminal):"
echo "  source .venv/Scripts/activate"
echo ""
echo "  # Customer Intelligence (port 9100)"
echo "  python agent.py \"Show me the 360 profile for CUST001\""
echo "  python agent.py \"What is CUST002's profitability and win rate?\""
echo ""
echo "  # Pricing Engine (port 9200)"
echo "  python agent.py \"Which deals are non-compliant and why?\""
echo "  python agent.py \"Explain step-by-step how the price was built for DEAL040\""
echo ""
echo "  # Both servers — multi-server fan-out + synthesis"
echo "  python agent.py \"Give me a comprehensive analysis of CUST001\""
echo "  python agent.py \"Should we approve a new deal with CUST002?\""
echo ""
echo "Press Ctrl+C to stop all servers."

# ---------------------------------------------------------------------------
# Graceful shutdown on Ctrl+C
# ---------------------------------------------------------------------------
cleanup() {
    echo ""
    echo "Stopping servers..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    # On Windows (Git Bash), free the ports if processes lingered
    if command -v taskkill &>/dev/null; then
        for port in 8001 8002 8003 8080 8090 9100 9200; do
            local_pid=$(netstat -ano 2>/dev/null | awk "/0.0.0.0:$port / {print \$NF}" | head -1)
            [ -n "$local_pid" ] && taskkill //PID "$local_pid" //F &>/dev/null || true
        done
    fi
    echo "All servers stopped."
}

trap cleanup EXIT INT TERM
wait
