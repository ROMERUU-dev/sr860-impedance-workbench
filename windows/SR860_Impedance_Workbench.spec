# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()

datas = [
    (str(project_root / "assets" / "srs-1.png"), "assets"),
    (str(project_root / "assets" / "srs-1.ico"), "assets"),
]

hiddenimports = [
    "pyvisa",
    "pyvisa_py",
    "matplotlib.backends.backend_tkagg",
]

a = Analysis(
    ["barrido.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SR860_Impedance_Workbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "srs-1.ico"),
)
