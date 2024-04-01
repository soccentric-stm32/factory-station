# Build a Windows MSI with PyInstaller + WiX Toolset (v3).
#
#   $env:PYTHON = "C:\path\to\python.exe"; packaging\build-windows.ps1
#
# The MSI needs WiX v3 (heat, candle and light), which is neither part of
# Windows nor of a stock build machine; the script stops before it installs
# anything when those are missing and points at `make build`, whose one-folder
# bundle in dist\hwval-factory\ is the artifact to ship without an installer.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# Prefer the project virtualenv; callers can override PYTHON.
$Python = $env:PYTHON
if (-not $Python) {
    if (Test-Path ".venv\Scripts\python.exe") {
        $Python = ".venv\Scripts\python.exe"
    } else {
        $Python = "python"
    }
}

foreach ($Tool in "heat", "candle", "light") {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "WiX Toolset v3 not found ($Tool is missing); install it from https://github.com/wixtoolset/wix3/releases, or run 'make build' for the one-folder bundle in dist\hwval-factory\ (no installer)."
    }
}

$Version = (& $Python -c "import sys; sys.path.insert(0,'src'); from hwval_factory import __version__; print(__version__)")
& $Python -m pip install -e . pyinstaller
& $Python -m PyInstaller --clean --noconfirm packaging/hwval-factory.spec

New-Item -ItemType Directory -Force -Path build\wix | Out-Null
Copy-Item packaging\windows\Product.wxs build\wix\Product.wxs
heat dir dist\hwval-factory -cg HwvalFactoryFiles -dr INSTALLFOLDER `
    -gg -g1 -sfrag -srd -sreg -var var.SourceDir -out build\wix\Files.wxs
candle -dVersion=$Version -dSourceDir=(Resolve-Path dist\hwval-factory) `
    -out build\wix\Product.wixobj build\wix\Product.wxs
candle -dVersion=$Version -dSourceDir=(Resolve-Path dist\hwval-factory) `
    -out build\wix\Files.wixobj build\wix\Files.wxs
light -ext WixUIExtension -out dist\HWVAL-Factory-$Version-x64.msi `
    build\wix\Product.wixobj build\wix\Files.wixobj
