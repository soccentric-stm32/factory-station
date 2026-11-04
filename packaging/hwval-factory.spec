# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for HWVAL Factory (PKG-001).

Run from the repository root::

    pyinstaller packaging/hwval-factory.spec

Collects the shared host core and the example profile so a freshly installed
station has a simulator profile to demonstrate the flow.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent.parent
datas = [
    (str(ROOT / "examples" / "profile-sc767-demo"), "examples/profile-sc767-demo"),
    (str(ROOT / "docs"), "docs"),
]
datas += collect_data_files("hwval_factory")
datas += collect_data_files("hostcore")

hiddenimports = (
    collect_submodules("hwval_factory")
    + collect_submodules("hostcore")
    + ["PIL._tkinter_finder"]
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hwval-factory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="hwval-factory",
)
