# Vendored: Hindsight memory provider

Upstream Hermes moved Hindsight out of core into the plugin catalog
(`plugin-catalog/hindsight.yaml`). This fork ships the catalog's reviewed pin
as a bundled provider instead, so a container never clones or pip-installs it
at first boot, and the code that runs is the code in this image.

- Source: https://github.com/vectorize-io/hindsight, `hindsight-integrations/hermes`
- Commit: `f14cc1eb5624b0374cb4d82768eb2e0b9c21ae24` (integrations/hermes/v1.2.4,
  the same pin as `plugin-catalog/hindsight.yaml`)
- Copied unmodified: `__init__.py`, `config_schema.py`, `embedded.py`,
  `settings.py`, `setup.py`, `templates.py`, `plugin.yaml`, `README.md`.
  Left out: `pyproject.toml`, `uv.lock`, `tests/`, `docs/`.
- Dependency: `hindsight-client` comes from this repo's `[hindsight]` extra
  (baked in the Docker image). `hindsight-embed` is only needed for
  `local_embedded` mode and is not installed.

To update: copy the same files from the new catalog pin, bump the commit
above, and move the `[hindsight]` extra to the Hindsight server's version.
