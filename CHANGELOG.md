# Changelog

## 0.1.0-rc.1 - 2026-09-18

### Release preparation

- Minimal runtime image with explicit source allowlist and OCI version/revision labels.
- Separate production Compose deployment: HTTPS Nginx, private backend/PostgreSQL,
  independent named volumes and generated credentials; local development stays separate.
- Stronger validation for production secrets and APP_ENV; development web binds localhost.
- CI for tests, migrations, security checks, runtime image, HTTPS smoke checks and PostgreSQL backup/restore.
- Manual, version-tagged GHCR publication after CI, initially linux/amd64 only.
- MIT license, installation, contribution, release and security policies.

### Upgrade notes

- No database schema migration is introduced by this release-preparation change.
- Production now rejects short/placeholder secrets; changing SECRET_KEY invalidates sessions.
- Local Compose web is no longer exposed on every host interface.
- deploy/compose.yml uses a separate database volume. Existing installations must follow
  the backup/restore migration procedure in docs/INSTALL.md, not delete old volumes.
- This is an evaluation release candidate, not a stable release. Use synthetic data
  first and validate migration from your existing installation on a separate copy.
