@echo off
setlocal

cd /d %~dp0\..

if not exist .venv-win (
    py -3 -m venv .venv-win
)

call .venv-win\Scripts\python.exe -m pip install --upgrade pip
call .venv-win\Scripts\pip.exe install -r windows\requirements-build.txt
call .venv-win\Scripts\pyinstaller.exe --noconfirm --clean windows\SR860_Impedance_Workbench.spec

echo.
echo Build completado.
echo Ejecutable: dist\SR860_Impedance_Workbench.exe
