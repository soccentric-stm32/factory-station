# Packaging

`packaging/hwval-factory.spec` builds a PyInstaller onedir bundle.  The
resulting executable is `dist/hwval-factory/hwval-factory`.

* Linux: `bash packaging/build-linux.sh` stages a `.deb` with `fpm` and an
  AppImage with `appimagetool`.  OpenOCD and the ST-LINK udev rules remain
  system packages.
* macOS: `bash packaging/build-macos.sh` builds `dist/hwval-factory.app` and
  `dist/HWVAL-Factory-<version>.dmg`.  Run it on macOS; `hdiutil` is part of the
  base system.
* Windows: `powershell -ExecutionPolicy Bypass -File packaging/build-windows.ps1`
  builds `dist/HWVAL-Factory-<version>-x64.msi` with WiX Toolset v3.  WiX v3
  (`heat`, `candle`, `light`) is not part of Windows, so the script stops with
  that message instead of failing inside the build; without it the one-folder
  bundle in `dist/hwval-factory/` from `make build` is the artifact to ship.

The bench tool keeps its factory-database CSV and other working files in the
folder given by `--data-dir` (default `station-data` in the current folder).
Keep it outside the installation directory, for example under
`%PROGRAMDATA%\HWVAL Factory` on Windows, `~/Library/Application Support/HWVAL
Factory` on macOS or `/var/lib/hwval-factory` on Linux.

## ST-LINK access on Linux (K-01)

Install the bundled udev rule so an operator can use the probe and its VCP
without root:

```bash
sudo install -m 0644 packaging/udev/99-stlink.rules \
  /etc/udev/rules.d/99-stlink.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Log out and back in if the user was just added to `plugdev`.  On Windows,
install ST-LINK/V2 drivers with STM32CubeProgrammer or the ST-LINK USB driver
package before attaching the probe.  macOS uses IOKit, so there is no udev rule
to install; the probe works once OpenOCD is on `PATH` (for example
`brew install openocd`).

## macOS build notes

```bash
bash packaging/build-macos.sh
```

The spec runs PyInstaller in the project virtualenv (`PYTHON` overrides it) and
wraps the one-folder payload in a Finder-launchable `.app`: the `BUNDLE` target
sets the bundle identifier `com.soccentric.hwval-factory` and the version in
`Info.plist`.  The script then stages the `.app` with an `/Applications` link
and calls `hdiutil` to write the `.dmg`.

* `packaging/hwval-factory.icns` is passed to the bundle when it exists.  It is
  rendered from the `packaging/hwval-factory.svg` master out of band; until that
  file is present the `.app` uses the default PyInstaller icon.
* The `.app` is unsigned and not notarised, so Gatekeeper blocks the first
  launch after a download.  Either right-click the app and choose *Open*, or run
  `xattr -dr com.apple.quarantine /Applications/HWVAL-Factory.app`.
* PyInstaller builds for the host architecture, so an Apple-silicon and an
  Intel `.dmg` have to be produced on their respective machines.
