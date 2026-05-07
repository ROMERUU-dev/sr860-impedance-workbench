$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv-win")) {
    py -3 -m venv .venv-win
}

& .\.venv-win\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-win\Scripts\pip.exe install -r .\windows\requirements-build.txt

& .\.venv-win\Scripts\pyinstaller.exe --noconfirm --clean .\windows\SR860_Impedance_Workbench.spec

Write-Host ""
Write-Host "Build completado."
Write-Host "Ejecutable: dist\SR860_Impedance_Workbench.exe"
