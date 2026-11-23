<#
.SYNOPSIS
  Create .venv (Python 3.12+) and install dependencies when requirements change.
.PARAMETER Dev
  Also install test/lint tools (requirements-dev.txt).
#>
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Find-Python {
    # Returns the command (and arguments) of a Python 3.12+ interpreter, or $null.
    $candidates = @("py -3.12", "py -3", "python", "python3")
    foreach ($c in $candidates) {
        $parts = $c.Split(" ")
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = @($parts | Select-Object -Skip 1)
        try {
            $v = & $parts[0] @pyArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]"3.12") { return $parts }
        } catch { }
    }
    return $null
}

$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) {
    $base = Find-Python
    if (-not $base) {
        Write-Host "Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH') and run this again." -ForegroundColor Red
        exit 1
    }
    Write-Host "Creating virtual environment with $($base -join ' ') ..."
    $rest = @($base | Select-Object -Skip 1)
    & $base[0] @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not create the virtual environment." -ForegroundColor Red; exit 1 }
}

$reqFiles = @("requirements.txt", "pyproject.toml")
if ($Dev) { $reqFiles += "requirements-dev.txt" }
$hash = ($reqFiles | ForEach-Object { (Get-FileHash -LiteralPath (Join-Path $Root $_) -Algorithm SHA256).Hash }) -join ""
$stamp = Join-Path $Venv (".installed-" + ($(if ($Dev) { "dev" } else { "app" })))
if ((Test-Path -LiteralPath $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $hash)) {
    Write-Host "Dependencies are up to date."
    exit 0
}
Write-Host "Installing dependencies (first run or requirements changed) ..."
& $Py -m pip install --disable-pip-version-check -q --upgrade pip
$req = $(if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" })
& $Py -m pip install --disable-pip-version-check -q -r (Join-Path $Root $req)
if ($LASTEXITCODE -ne 0) { Write-Host "Dependency installation failed. Check your internet connection and retry." -ForegroundColor Red; exit 1 }
& $Py -m pip install --disable-pip-version-check -q --no-deps -e $Root
if ($LASTEXITCODE -ne 0) { Write-Host "Installing the ReconcileAI package failed." -ForegroundColor Red; exit 1 }
Set-Content -LiteralPath $stamp -Value $hash -Encoding ascii
Write-Host "Dependencies installed."
