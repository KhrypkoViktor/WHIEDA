# Private partner-library storage

## Provider decision

Use Contabo Object Storage in the European Union for staging and production.
It is external to the Core VPS, S3-compatible, inexpensive at the current scale,
and supports private objects plus pre-signed downloads.

Endpoint: `https://eu2.contabostorage.com`.
Addressing style: `path` (required by Contabo-compatible clients).

Do not deploy MinIO on the Core host. Core already runs n8n, Dify, PostgreSQL,
Redis and several API containers; course files must not compete with them for
memory, disk IO or backups.

## One manual account step

In the Contabo panel order the smallest EU Object Storage package and create one
private bucket. Suggested name: `wwc-best-private-library`; if it is unavailable,
use another DNS-safe name and put that exact name in the env.

Retrieve the S3 access key and secret under `Account -> Security & Access`.
Never send them through Telegram and never commit them.

## Staging env

Copy the variables from `partner-library-s3.env.example` into the staging Core
`.env`. Set its mode to `0600`. Staging and production may share the Object
Storage account, but use different buckets. Production credentials are added
only during a separately approved production release.

## Verification

From the Platform API container/environment:

```bash
python scripts/partner_library_s3.py check
python scripts/partner_library_s3.py probe --tenant-id whieda
```

`check` is read-only and refuses a public bucket ACL. `probe` writes one random
small object, downloads it through a 60-second pre-signed URL and deletes it.
Neither command prints credentials.

## Uploading a material

```bash
python scripts/partner_library_s3.py upload \
  --tenant-id whieda \
  --file /secure/source/welcome.pdf \
  --key whieda/presentations/welcome.pdf
```

The key must begin with the tenant ID. Objects are uploaded with a private ACL.
After upload, import the matching library manifest into PostgreSQL. Browser users
receive only short-lived pre-signed URLs after Core rechecks paid access.
