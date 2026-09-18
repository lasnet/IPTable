# Security policy

IPtable is intended for trusted LAN/VPN deployments, not anonymous public hosting.
Publishing its source or container image does not require publishing your live instance.

## Supported releases

The first versioned release is being prepared. No long-term support or security SLA
is promised. Once releases are available, use the latest supported stable patch;
release candidates are for evaluation on isolated data, not a stability guarantee.

## Reporting a vulnerability

Use the repository's **Security -> Report a vulnerability** private reporting feature
when enabled. Do not open a public issue containing an exploit, credentials, IP
inventory, logs with personal information, database dumps or session cookies.
If private reporting is unavailable, open an issue requesting a private contact
without disclosing vulnerability details. The maintainer must enable private reporting
before public promotion; no unverified email address is provided here.

Include the affected release/digest, configuration with secrets removed, reproduction
steps using synthetic data and the expected impact. Coordinate disclosure with the
maintainer. If a credential was exposed, revoke/rotate it immediately, including
copies in backups and history where relevant.

## Operator responsibilities

- Use deploy/compose.yml, HTTPS and separate production secrets/volumes.
- Keep the instance restricted to LAN/VPN and trusted users; do not expose PostgreSQL.
- Do not connect untrusted containers to the application networks. The web process
  trusts proxy headers on these private networks; Nginx replaces forwarded headers.
- Maintain off-host encrypted backups and test recovery before database upgrades.
- Review dependency/image advisories, changelogs, audit output and TLS certificate expiry.
- Development defaults and CI certificates are not production credentials or trust roots.
