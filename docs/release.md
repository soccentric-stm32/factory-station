# HWVAL Factory — release process

1. Update the version in `pyproject.toml` and `src/hwval_factory/__init__.py`.
2. Run the gates locally:

   ```bash
   make lint
   make typecheck
   make test
   make smoke
   ```

3. Build a profile from the released firmware artefacts:

   ```bash
   hwval-profile build profiles/sc767-X.Y.Z --from-workspace \
     --id sc767 --version X.Y.Z --product "SoCcentric STM32F767ZI board" \
     --hw-revisions B C
   hwval-profile verify profiles/sc767-X.Y.Z
   ```

   The manifest pins the SHA-256 of `boot.hex`, `hwval.elf` and the IBWH
   `app.img`. Keep `min_factory_version` at the oldest station that may run
   the profile; an older station refuses a newer profile.

4. Package and sign:

   ```bash
   make build                 # PyInstaller bundle
   make package-linux         # or package-windows on Windows
   hwval-profile sign profiles/sc767-X.Y.Z keys/eng-private.pem
   hwval-profile archive profiles/sc767-X.Y.Z dist/sc767-X.Y.Z.zip
   ```

5. Record the release in the changelog. Attach the package, the profile
   archive, `SHA256SUMS` and the public signing key. Never attach the private
   key or the MES token.

6. On the line, import the profile, set `require_profile_signature: true` and
   the trusted public key, then run `--probe`, the golden-board self-test and
   one first-article unit before releasing the station.

A station refuses a profile whose `min_factory_version` is newer than the
installed factory version, and refuses unsigned profiles when signatures are
required.
