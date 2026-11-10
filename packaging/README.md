# Packaging

`packaging/hwval-factory.spec` builds a PyInstaller onedir bundle.  The
resulting executable is `dist/hwval-factory/hwval-factory`.

* Linux: `bash packaging/build-linux.sh` stages a `.deb` with `fpm` and an
  AppImage with `appimagetool`.  OpenOCD and the ST-LINK udev rules remain
  system packages.
* Windows: `powershell -ExecutionPolicy Bypass -File packaging/build-windows.ps1`
  builds `dist/HWVAL-Factory-<version>-x64.msi` with WiX Toolset v3.

The bench tool keeps its factory-database CSV and other working files in the
folder given by `--data-dir` (default `station-data` in the current folder).
Keep it outside the installation directory, for example under
`%PROGRAMDATA%\HWVAL Factory` on Windows or `/var/lib/hwval-factory` on Linux.

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
package before attaching the probe.
