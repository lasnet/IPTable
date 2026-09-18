# Changelog

## Unreleased

### Release preparation

- Minimal runtime image with explicit source allowlist and OCI version/revision labels.
- Separate production Compose deployment: HTTPS Nginx, private backend/PostgreSQL,
  independent named volumes and generated credentials; local development stays separate.
- Stronger validation for production secrets and APP_ENV; development web binds localhost.
- CI for tests, migrations, security checks, runtime image and HTTPS smoke checks.
- Manual, version-tagged GHCR publication after CI, initially linux/amd64 only.
- MIT license, installation, contribution, release and security policies.

### Upgrade notes

- No database schema migration is introduced by this release-preparation change.
- Production now rejects short/placeholder secrets; changing SECRET_KEY invalidates sessions.
- Local Compose web is no longer exposed on every host interface.
- deploy/compose.yml uses a separate database volume. Existing installations must follow
  the backup/restore migration procedure in docs/INSTALL.md, not delete old volumes.
- No image/tag/release has been published by adding these files. Record the actual first
  RC version and date here only after successful validation and release approval.
