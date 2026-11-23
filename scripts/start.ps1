<#
.SYNOPSIS
  Start ReconcileAI locally (http://localhost:8501). Data stays on this computer.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "setup.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location -LiteralPath $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
if (-not (Test-Path -LiteralPath (Join-Path $Root "demo\data\orders.xlsx"))) {
    & $Py -m reconcileai.cli demo --out demo | Out-Null
}
Write-Host "Starting ReconcileAI at http://localhost:8501  (press Ctrl+C in this window to stop)"
$headless = $(if ($env:RECONCILEAI_HEADLESS) { "true" } else { "false" })  # tests set this to skip opening a browser
& $Py -m streamlit run (Join-Path $Root "app.py") --server.headless $headless
exit $LASTEXITCODE
