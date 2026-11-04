# Build a Windows MSI with PyInstaller + WiX Toolset (v3).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Version = (python -c "import sys; sys.path.insert(0,'src'); from hwval_factory import __version__; print(__version__)")
python -m pip install -e . pyinstaller
pyinstaller --clean --noconfirm packaging/hwval-factory.spec

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
