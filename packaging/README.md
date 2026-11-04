# Packaging

`packaging/hwval-factory.spec` builds a PyInstaller onedir bundle.  The
resulting executable is `dist/hwval-factory/hwval-factory`.

* Linux: `bash packaging/build-linux.sh` stages a `.deb` with `fpm` and an
  AppImage with `appimagetool`.  OpenOCD and the ST-LINK udev rules remain
  system packages.
* Windows: `powershell -ExecutionPolicy Bypass -File packaging/build-windows.ps1`
  builds `dist/HWVAL-Factory-<version>-x64.msi` with WiX Toolset v3.

Station configuration, profiles, the SQLite database, labels and certificates
live outside the installation directory (PKG-002).  Keep them under
`%PROGRAMDATA%\HWVAL Factory` on Windows or `/var/lib/hwval-factory` on Linux,
or pass `--station` to the executable.  A station is reproducible from the
installer, `station.yaml` and a profile archive (PKG-004).
