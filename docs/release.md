# HWVAL Factory — release process

1. Update the version in `pyproject.toml` and `src/hwval_factory/__init__.py`.
2. Run the gates locally:

   ```bash
   make lint
   make typecheck
   make test
   make smoke
   ```

3. Package:

   ```bash
   make build                 # PyInstaller bundle
   make package-linux         # or package-windows on Windows
   ```

4. Record the release in the changelog and attach the package and
   `SHA256SUMS`. Never attach a private signing key.

5. Tag the release as `v<version>` and push the tag; Jenkins publishes `dist/`
   as a GitHub release.
