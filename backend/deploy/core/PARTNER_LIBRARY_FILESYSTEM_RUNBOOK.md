# Private partner-library filesystem MVP

## Placement

Store files on the Core VPS, not on the static website VPS:

- host directory: `/var/lib/wwc-partner-library`;
- container directory: `/var/lib/wwc-partner-library`;
- tenant prefix: `/var/lib/wwc-partner-library/whieda/`;
- directory is outside every nginx document root;
- API container mounts it read-only.

The website remains static and receives no filesystem or nginx changes. Core
checks the paid Telegram session, creates a five-minute signed URL and streams
the selected file. A copied URL expires automatically.

## Server preparation

```bash
install -d -m 0750 -o root -g root /var/lib/wwc-partner-library/whieda
openssl rand -hex 32
```

Put the generated value and the other variables from
`partner-library-filesystem.env.example` into the staging Core `.env`. Keep the
file mode `0600`. Never put the secret in Git, Telegram or an operator report.

The staging compose must mount the host directory read-only into the API
container. The worker does not need the files.

## File placement

Every file path must begin with the tenant ID. Example:

```text
/var/lib/wwc-partner-library/whieda/presentations/welcome.pdf
```

Upload into a temporary sibling path, verify size and SHA-256, then rename into
place. Files use mode `0640`; directories use `0750`. Do not put files inside
the repository, Docker image, static site `dist` or an nginx web root.

After placement, import a manifest whose `storage_key` matches the relative path:

```text
whieda/presentations/welcome.pdf
```

## Canary

1. Rebuild and restart staging API only.
2. Confirm `/health/ready` is `200`.
3. Import one draft fixture and verify it is absent from the public list.
4. Publish the fixture and log in with an active test subscription.
5. Request its download URL and download the exact bytes.
6. Change one character in the token: expect `403`.
7. Wait past TTL: expect `403`.
8. Use a suspended subscription: list/download must return `403`.

## Backup and later S3 migration

Back up the directory separately from application releases. The database stores
only tenant-scoped storage keys, so a later switch to S3 keeps the same keys and
requires no website or database redesign.
