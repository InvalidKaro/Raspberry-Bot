# HomePi VPN Manager (Phase 1: read-only)

The dashboard at `/vpn` and owner-only Discord commands `/vpn status` and `/vpn locations` inspect local WireGuard interfaces and profile filenames. No profile contents or secrets are returned.

This phase deliberately does **not** install WireGuard, start or stop interfaces, change default routes, enable forwarding, alter firewall rules, change DNS, modify Pi-hole, or modify Tailscale. No country or free exit server is promised. The dashboard inherits the existing session authentication; the Discord group is restricted to the configured owner IDs and the HomePi guild.

Optional: `HOMEPI_VPN_PROFILE_DIR=/etc/wireguard`. Files must be regular non-symlink `.conf` files with safe interface names. The service account needs only directory listing permission, not read access to private keys.

Before enabling any future switch action, test routing and DNS in an isolated network namespace, validate SSH/Tailscale/Pi-hole reachability, configure fail-closed client routing with a tested rollback timer, and provide a trusted VPN exit with a verified country. Never install privileged generic shell execution in the dashboard or bot.
