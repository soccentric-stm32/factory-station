#!/usr/bin/env bash
# Build a Linux .deb and (if appimagetool is available) an AppImage.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(python3 -c 'import sys; sys.path.insert(0,"src"); from hwval_factory import __version__; print(__version__)')"
python3 -m pip install -e . pyinstaller
pyinstaller --clean --noconfirm packaging/hwval-factory.spec

STAGE="$ROOT/build/deb/hwval-factory"
rm -rf "$STAGE"
mkdir -p "$STAGE/opt/hwval-factory" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/hwval-factory"
cp -R dist/hwval-factory/. "$STAGE/opt/hwval-factory/"
ln -s /opt/hwval-factory/hwval-factory "$STAGE/usr/bin/hwval-factory"
cp packaging/hwval-factory.desktop "$STAGE/usr/share/applications/"
cp README.md docs/*.md "$STAGE/usr/share/doc/hwval-factory/"

if command -v fpm >/dev/null 2>&1; then
  fpm -s dir -t deb -n hwval-factory -v "$VERSION" -C "$STAGE" \
      --description "HWVAL Factory end-of-line test and provisioning" \
      --url "https://github.com/soccentric-stm32/factory-station" \
      --maintainer "Sandesh Ghimire <sandesh@soccentric.com>" \
      --depends "openocd (>= 0.12)" \
      .
elif command -v dpkg-deb >/dev/null 2>&1; then
  ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
  mkdir -p "$STAGE/DEBIAN"
  cat > "$STAGE/DEBIAN/control" <<EOF
Package: hwval-factory
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: openocd (>= 0.12)
Maintainer: Sandesh Ghimire <sandesh@soccentric.com>
Homepage: https://github.com/soccentric-stm32/factory-station
Description: HWVAL Factory end-of-line test and provisioning
 STM32F767ZI end-of-line test and provisioning station.
EOF
  dpkg-deb --build --root-owner-group "$STAGE" \
      "$ROOT/dist/hwval-factory_${VERSION}_${ARCH}.deb"
else
  echo "neither fpm nor dpkg-deb found: deb staging directory is in $STAGE" >&2
fi

if command -v appimagetool >/dev/null 2>&1; then
  APPDIR="$ROOT/build/HWVAL-Factory.AppDir"
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
           "$APPDIR/usr/share/icons/hicolor/256x256/apps"
  cp -R dist/hwval-factory/. "$APPDIR/usr/bin/"
  cp packaging/hwval-factory.desktop "$APPDIR/"
  cp packaging/hwval-factory.desktop "$APPDIR/usr/share/applications/"
  cp packaging/hwval-factory.svg "$APPDIR/hwval-factory.svg"
  cp packaging/hwval-factory.svg "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
  appimagetool "$APPDIR" "$ROOT/dist/HWVAL-Factory-$VERSION-x86_64.AppImage"
else
  echo "appimagetool not found: AppImage not built" >&2
fi
