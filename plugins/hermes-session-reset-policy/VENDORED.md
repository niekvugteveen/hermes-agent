# Vendored: session reset policy

Upstream Hermes stopped rotating gateway conversations on the `session_reset`
timers (v2026.9.11); the catalog plugin `hermes-session-reset-policy` reads the
same block and restores the behaviour. This fork ships the catalog's reviewed
pin in-tree so no container installs it at runtime. It still has to be listed
in `plugins.enabled` (bundled standalone plugins are opt-in).

- Source: https://github.com/fastfinge/hermes-session-reset-policy
- Commit: `0f2ea5ad995f1e4ba7c92c2dbd21afa868b8ae21` (v0.3.0, the pin in
  `plugin-catalog/hermes-session-reset-policy.yaml`)
- Copied unmodified: `__init__.py`, `plugin.yaml`, `README.md`, `LICENSE`.
  No runtime dependencies.
