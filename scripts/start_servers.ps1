<#
.SYNOPSIS
    Start all MCP servers for the FAB MCP Hub.
.DESCRIPTION
    Launches Weather (8001), Calculator (8002), Data Lookup (8003),
    FAB Customer Intelligence (9100), FAB Pricing Engine (9200),
    Hub Server (8090), and Chat UI (8080) servers.
    Uses the Python from the local .venv (packages must be installed).
    Press Ctrl+C to stop all servers.
.NOTES
    Requires Ollama running locally: ollama serve
    The FAB Data Layer server starts without MySQL but tools will fail
    unless MYSQL_USER and MYSQL_PASSWORD are set in datalayer-as-service/.env
#>

$Root = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Resolve Python — prefer local .venv, fall back to sibling venv or system
# ---------------------------------------------------------------------------
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $SiblingPython = Join-Path $Root "..\fab-mcp-hub\.venv\Scripts\python.exe"
    if (Test-Path $SiblingPython) {
        $VenvPython = $SiblingPython
    } else {
        Write-Host "WARNING: .venv not found — using system 'python'." -ForegroundColor Yellow
        $VenvPython = "python"
    }
}
Write-Host "Using Python: $VenvPython" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Start servers
# ---------------------------------------------------------------------------
$processes = @()

function Start-McpServer {
    param($Name, $CmdArgs, $WorkDir)
    Write-Host "  Starting $Name..." -ForegroundColor Yellow
    $proc = Start-Process $VenvPython -ArgumentList $CmdArgs `
        -PassThru -NoNewWindow -WorkingDirectory $WorkDir
    Write-Host "    PID $($proc.Id)" -ForegroundColor Gray
    $proc
}

Write-Host ""
Write-Host "Clearing ports 8001 8002 8003 8080 8090 9100 9200 (killing stale processes)..." -ForegroundColor DarkGray
foreach ($hubPort in @(8001, 8002, 8003, 8080, 8090, 9100, 9200)) {
    $lines = netstat -ano 2>$null | Select-String ":$hubPort "
    foreach ($line in $lines) {
        $portPid = ($line.Line -split '\s+') | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1
        if ($portPid -match '^\d+$') {
            Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
        }
    }
}
Start-Sleep -Milliseconds 800
Write-Host ""
Write-Host "Starting MCP servers..." -ForegroundColor Cyan

# Demo servers — scripts live in datalayer-as-service\mcp_server\
# MCP_SERVER_ID identifies this server in the hub's mcp_servers table so it
# can load its own per-server api_key from MySQL (overrides shared MCP_API_KEY).
$env:MCP_SERVER_ID = "weather-server"
$processes += Start-McpServer "Weather Server    (port 8001)" "datalayer-as-service\mcp_server\weather_server.py 8001" $Root
Start-Sleep -Milliseconds 1000
$env:MCP_SERVER_ID = "calculator-server"
$processes += Start-McpServer "Calculator Server (port 8002)" "datalayer-as-service\mcp_server\calc_server.py 8002"    $Root
Start-Sleep -Milliseconds 1000
$env:MCP_SERVER_ID = "data-server"
$processes += Start-McpServer "Data Server       (port 8003)" "datalayer-as-service\mcp_server\data_server.py 8003"    $Root
Start-Sleep -Milliseconds 1000

# FAB Data Layer servers — must run from datalayer-as-service/ so Python finds the mcp_server package
$DataLayerDir = Join-Path $Root "datalayer-as-service"

$env:MCP_TRANSPORT = "http"
$env:MCP_HOST      = "127.0.0.1"
$env:MCP_PORT      = "9100"
$env:MCP_SERVER_ID = "fab-customer-server"
$processes += Start-McpServer "FAB Customer Intelligence (port 9100)" "-m mcp_server.customer_server" $DataLayerDir
Start-Sleep -Milliseconds 2000

$env:MCP_PORT      = "9200"
$env:MCP_SERVER_ID = "fab-pricing-server"
$processes += Start-McpServer "FAB Pricing Engine        (port 9200)" "-m mcp_server.pricing_server" $DataLayerDir
Start-Sleep -Milliseconds 2000

# Hub Server — REST routing/discovery API on port 8090 (REQUIRED before agent.py)
$env:HUB_PORT       = "8090"
$env:HUB_SERVER_URL = "http://localhost:8090"
$processes += Start-McpServer "Hub Server                (port 8090)" "hub_service\hub_server.py" $Root
Start-Sleep -Milliseconds 2000

# Chat UI server — serves the web UI on port 8080
$env:CHAT_PORT = "8080"
$processes += Start-McpServer "Chat UI Server            (port 8080)" "chat_service\chat_server.py" $Root
Start-Sleep -Milliseconds 1500

Write-Host ""
Write-Host "All servers started." -ForegroundColor Green
Write-Host ""
Write-Host "Endpoints:"
Write-Host "  Weather:                   http://localhost:8001/sse       [SSE]"
Write-Host "  Calculator:                http://localhost:8002/sse       [SSE]"
Write-Host "  Data Lookup:               http://localhost:8003/sse       [SSE]"
Write-Host "  FAB Customer Intelligence: http://127.0.0.1:9100/mcp/     [streamable-HTTP]"
Write-Host "  FAB Pricing Engine:        http://127.0.0.1:9200/mcp/     [streamable-HTTP]"
Write-Host "  Hub Server:                http://localhost:8090/health    [REST API]"
Write-Host "  Chat UI:                   http://localhost:8080            [Web Browser]"
Write-Host ""
Write-Host "MySQL: 127.0.0.1:3306  test_user / fab_semantic  (already configured)"
Write-Host ""
Write-Host "Chat UI (open in browser):"
Write-Host "  http://localhost:8080"
Write-Host ""
Write-Host "Run agent CLI (new terminal):"
Write-Host ""
Write-Host "  # Customer Intelligence server (port 9100)"
Write-Host "  python agent.py ""Show me the 360 profile for CUST001"""
Write-Host "  python agent.py ""What is CUST002's profitability and win rate?"""
Write-Host ""
Write-Host "  # Pricing Engine server (port 9200)"
Write-Host "  python agent.py ""Which deals are non-compliant and why?"""
Write-Host "  python agent.py ""Explain step-by-step how the price was built for DEAL040"""
Write-Host ""
Write-Host "  # Both servers (multi-server fan-out + synthesis)"
Write-Host "  python agent.py ""Give me a comprehensive analysis of CUST001"""
Write-Host "  python agent.py ""Should we approve a new deal with CUST002?"""
Write-Host ""
Write-Host "Press Ctrl+C to stop all servers." -ForegroundColor DarkGray

try {
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    Write-Host ""
    Write-Host "Stopping servers..." -ForegroundColor Yellow
    foreach ($proc in $processes) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "All servers stopped." -ForegroundColor Cyan
}
