# Partner edge-map sync

This package prepares the paid partner-host gate. It must be canaried on the
isolated `127.0.0.1:8443` listener before any production vhost is edited.

## Files on the site VPS

- `/opt/wwc-edge/sync_partner_host_map.py`, owned by root and not writable by the service;
- `/etc/wwc/partner-edge-sync.env`, mode `0600`;
- `/etc/nginx/snippets/wwc-partner-access-map.conf`, generated last-known-good map;
- `/etc/nginx/snippets/wwc-partner-access-gate.conf`, server-level 302 gate;
- `/var/lib/wwc-edge/partner-host-map-state.json`, last applied snapshot metadata.

Create the two destination directories before enabling the unit. The systemd
service is intentionally unable to write elsewhere.

## Nginx placement

1. Include `wwc-partner-access-map.conf` exactly once inside `http {}`.
2. Include `wwc-partner-access-gate.conf` only inside the wildcard personal-site
   vhost after exact technical vhosts have been separated.
3. Do not include the gate in `wwc.best`, `www`, `staging`, `admin`, API or media vhosts.
4. The redirect uses `$uri`, so the path is retained and the query string is discarded.

The first canary uses `wwc-partner-edge-staging.conf.example` on the isolated
loopback listener. It must prove an active host returns 204 and a suspended or
unknown host returns `302 https://wwc.best/<same-path>` without query parameters.

## Failure contract

The sync refuses an invalid signature, wrong tenant, stale/future timestamp,
unknown schema, invalid/reserved hostname, duplicate host, mismatched version or
empty list. It validates the candidate independently, replaces the map atomically,
runs the full `nginx -t`, then reloads. A failure restores the previous bytes and
does not write new state. The secret is never accepted as a command-line value.

No command in this runbook changes DNS, TLS, HTTP/2, gzip or the public `:443`
listener. Production activation requires a separate owner decision after staging.
