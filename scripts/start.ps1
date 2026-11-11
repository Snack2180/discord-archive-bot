param([switch]$Check)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run: py -3.11 -m venv .venv; then install dependencies as described in README.md' }
if ($Check) { & $pythonExe -m discord_archive.app --check; exit $LASTEXITCODE }
if (-not $env:DISCORD_BOT_TOKEN) {
    $secret = Read-Host 'Discord bot token (local only)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { $env:DISCORD_BOT_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
try { & $pythonExe -m discord_archive.app }
finally { Remove-Item Env:DISCORD_BOT_TOKEN -ErrorAction SilentlyContinue }
