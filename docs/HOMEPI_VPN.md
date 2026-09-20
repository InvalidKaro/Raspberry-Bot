# HomePi VPN Manager — opt-in WireGuard application proxy

This manager is intentionally **not a host-wide VPN or LAN gateway**. It runs the open-source `wireproxy` userspace WireGuard client as a SOCKS5 proxy on **127.0.0.1:25344 only**. Applications on the Raspberry Pi that are configured with `socks5h://127.0.0.1:25344` use the selected WireGuard provider; all other Pi apps continue to use their current connection. No default routes, Linux network namespaces, firewall/NAT rules, SSH, Pi-hole DNS settings or Tailscale exit-node settings are changed by this implementation. VPN authentication and DNS forwarding depend on the WireGuard provider profile and on the application's correct use of SOCKS5 remote DNS.

## Install prerequisites (manual; no automatic network changes)

1. Obtain a valid WireGuard client configuration and permission to use a VPN **exit server** for each desired country. A locally named `de.conf` does **not** prove that its server exits in Germany. Wireproxy itself is free/open source; a multi-country VPN service is **not** included or guaranteed to be free. Do not commit profiles or keys to GitHub.
2. Install `wireproxy` from its upstream repository: https://github.com/windtf/wireproxy . For example, on a Pi with an existing supported Go installation, `go install github.com/windtf/wireproxy/cmd/wireproxy@v1.1.2` followed by `sudo install -m 755 "$HOME/go/bin/wireproxy" /usr/local/bin/wireproxy`. Check the upstream project and release provenance yourself; **do not** run unattended install scripts from third parties. Ensure `wireproxy -h` works for the dashboard service account. The code requires `wireproxy -n -c CONFIG` configuration validation before starting the proxy.
3. With the same Linux account used by the dashboard service, create the private profile directory:

   ```bash
   install -d -m 700 "$HOME/.config/homepi-vpn/profiles"
   install -m 600 /path/to/provider-configuration.conf "$HOME/.config/homepi-vpn/profiles/de-frankfurt.conf"
   ```

   Edit the profile **locally** to include only `[Interface]` and **one** `[Peer]`; accepted fields: interface `Address`, `PrivateKey`, `DNS`, `MTU`; peer `PublicKey`, `PresharedKey`, `AllowedIPs`, `Endpoint`, `PersistentKeepalive`. Set DNS to a numeric address supported by the provider and require `AllowedIPs = 0.0.0.0/0`. `PostUp`, `PostDown`, `WGConfig`, user-defined proxy bind addresses, environment-variable key references and other directives are not accepted. The manager generates a temporary private wireproxy configuration, adding a loopback-only SOCKS5 listener.
4. Optional **user-entered, unverified labels** in `$HOME/.config/homepi-vpn/locations.json`:

   ```json
   {"de-frankfurt": {"country": "DE", "city": "Frankfurt"},
    "nl-amsterdam": {"country": "NL", "city": "Amsterdam"}}
   ```

   The labels are never verified and only correspond to locally installed profile names. Profile names may contain lowercase letters, digits, hyphens and underscores (up to 31 chars).
5. Switch to this feature branch and restart the dashboard and bot *only after reviewing your local changes and backups*. No VPN starts automatically on boot. Dashboard: `/vpn`; owner-only Discord commands on your configured HomePi guild: `/vpn status`, `/vpn locations`, `/vpn connect <profile>`, `/vpn disconnect`. Both services must use the **same Linux user** and project directory for the private Unix control socket. If the dashboard isn't running, Discord VPN controls cannot connect.

## Use and verification

Click **Verbinden** in the authenticated dashboard or run `/vpn connect` as the Discord owner; this is an explicit opt-in action. The startup check confirms that wireproxy accepted the configuration and opened the loopback SOCKS5 listener; it **does not verify** a successful WireGuard handshake or the provider's exit location.

Configure an individual app with **remote DNS** (`socks5h`, not `socks5`). For a quick on-Pi outbound check, compare:

```bash
curl --max-time 12 https://api.ipify.org
curl --max-time 12 --socks5-hostname 127.0.0.1:25344 https://api.ipify.org
```

Verify the exit IP and country with a trusted independent lookup; do not assume the profile label is genuine. If `curl` fails through the proxy, the WireGuard tunnel may not have established. The SOCKS5 listener is unauthenticated **to other local accounts on the Pi**, so keep the host's Linux users trusted; it is not accessible from the LAN by default.

**Limitations:** No automatic VPN for Windows/iPhone, whole-LAN forwarding, DNS filtering for remote client devices or kill switch for unrelated applications. A non-proxy-aware app cannot use this mode. Disconnection, dashboard restart and failed profile switches close the managed proxy; correctly configured proxy-only apps should fail closed rather than silently switch to direct traffic (confirm each application's fallback behavior). Existing connections through the previous proxy may break on switch. A stopped dashboard cannot keep or manage the proxy. The current implementation cannot promise every site's or application's traffic will use the selected country.

## Security and troubleshooting

- The dashboard retains its existing session and CSRF protections; the Discord commands check the HomePi guild and `OWNER_IDS` and respond ephemerally. Discord communicates with the dashboard over an owner-only local Unix socket, not an exposed HTTP control API.
- Private profiles must be regular, non-symlink files owned by the dashboard user with mode `0600` and at most 16 KiB. Private generated configurations live under `data/vpn-runtime/` (mode `0700`), which must stay Git-ignored. Never attach these files to issues, screenshots or logs.
- `HOMEPI_VPN_PROFILE_DIR` can override the default profile directory; the **dashboard's** environment determines profile visibility and keys. `HOMEPI_VPN_RUNTIME_DIR` can override the runtime directory, but the bot and dashboard must agree on it. Ensure `data/vpn-runtime/` is private and not served by any web/static file route.
- If the dashboard is restarted while a profile is active, its managed wireproxy process is stopped during cleanup; it is never configured as an OS-wide service.
- This design avoids modifying the existing network; it is **not** a tested replacement for a system-wide WireGuard VPN. A later whole-device/LAN VPN must have separate SSH/Tailscale/DNS reachability checks, firewall review, test environment and automated rollback before enabling it.
