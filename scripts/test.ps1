<#
.SYNOPSIS
  Lint, run the test suite and the CLI smoke test.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "setup.ps1") -Dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location -LiteralPath $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
& $Py -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("reconcileai_smoke_" + [guid]::NewGuid())
& $Py -m reconcileai.cli demo --out $tmp --orders 200
& $Py -m reconcileai.cli run --orders (Join-Path $tmp "data\orders.xlsx") --bank (Join-Path $tmp "data\bank_statement.xlsx") --output (Join-Path $tmp "reports")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py -m reconcileai.cli evaluate (Join-Path $tmp "ground_truth.json") --min-precision 0.99
exit $LASTEXITCODE
