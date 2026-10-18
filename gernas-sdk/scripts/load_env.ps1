# Load selected variables from an env file into the current PowerShell session, without printing values.
#
#   . .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_,OPIK_
#
# Dot-source it so the variables stay in the calling shell. Used to give processes that do not
# read agent-mesh/.env themselves (DataLayer MCP, RAG MCP, scripts/opik_verify.py) the same
# GERNAS_* identity and endpoint. Prints only the names it set.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string[]]$Prefix = @('GERNAS_')
)
$names = @()
foreach ($line in Get-Content -LiteralPath $Path) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $key, $value = $line -split '=', 2
    $key = $key.Trim()
    if (-not ($Prefix | Where-Object { $key.StartsWith($_) })) { continue }
    $value = ($value -replace '\s+#.*$', '').Trim().Trim('"').Trim("'")
    if ($value) { Set-Item -Path "env:$key" -Value $value; $names += $key }
}
Write-Host "loaded: $($names -join ', ')"
