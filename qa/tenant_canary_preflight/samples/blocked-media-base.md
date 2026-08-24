# Tenant canary preflight

- state: `blocked_media`
- tenant: `tenant-north`
- mode: `plan`
- ok: `false`

## Counts

- products: 1
- approved: 1
- eligible: 1
- review_required: 0
- blocked: 0
- price_covered: 1
- approved_rows: 1

## Findings

- `media` / `media_base_url_invalid` / -: media_base_url must be HTTPS without localhost, Drive, wwc.best or other forbidden hosts
