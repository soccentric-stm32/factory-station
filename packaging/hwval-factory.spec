# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for HWVAL Factory (PKG-001).

Run from the repository root::

    pyinstaller packaging/hwval-factory.spec

Collects the shared host core and the bench guide.  On macOS the same run also
emits `dist/hwval-factory.app`, which `packaging/build-macos.sh` wraps in a
`.dmg`.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
from hwval_factory import __version__ as VERSION  # noqa: E402  (spec is exec'd by PyInstaller)

# Only used by the macOS bundle.  The SVG master is the source of truth; this
# file is rendered from it out of band (see packaging/README.md, "macOS build
# notes") and the bundle falls back to the PyInstaller icon while it is absent.
ICNS = ROOT / "packaging" / "hwval-factory.icns"
# The bench guide is optional: it ships next to README.md but is not part of
# every checkout, and `open_guide()` in the main window already falls back to a
# message when it is absent.  Adding a missing tree makes PyInstaller abort, so
# only collect it when it is really there.
DOCS = ROOT / "docs"
datas = []
if DOCS.is_dir():
    datas.append((str(DOCS), "docs"))
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

# Linux and Windows ship the one-folder output above.  macOS wraps that same
# onedir payload in a Finder-launchable .app, which is what adds the bundle
# identifier and the version in Info.plist.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="hwval-factory.app",
        icon=str(ICNS) if ICNS.is_file() else None,
        bundle_identifier="com.soccentric.hwval-factory",
        info_plist={
            "CFBundleName": "HWVAL Factory",
            "CFBundleDisplayName": "HWVAL Factory",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
        },
    )
