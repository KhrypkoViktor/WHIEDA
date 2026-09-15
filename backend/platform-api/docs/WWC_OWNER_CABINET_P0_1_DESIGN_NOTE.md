# WWC Owner Cabinet P0.1 — design note

**Date:** 2026-08-09  
**Scope:** Telegram challenge login + HttpOnly session cookie (no browser secrets).

## Flow

1. Browser `POST /v1/admin/auth/challenge` with `browser_nonce` (≥16 chars, kept in memory only).
2. Core stores `sha256(challenge_token)` + `sha256(browser_nonce)` in `platform_admin_login_challenges` (TTL from env).
3. Response returns `challenge_id`, `deep_link` (`t.me/{bot}?start=admin_login_{token}`). Raw challenge token is **not** returned in JSON.
4. **P0.1B ingress:** действующий Telegram webhook (`/v1/telegram/{binding_id}/webhook` → `_process_telegram_update`) и Core-процессор (`process_core_telegram_update`) перехватывают `/start admin_login_*` **до** legacy n8n и до обычного `handle_start_token`, вызывая `confirm_login_from_telegram` server-side (без HTTP confirm secret в браузере).
5. Альтернативный internal HTTP: `POST /v1/admin/auth/telegram-confirm` с header `X-Platform-Admin-Secret` (ops/scripts only).
6. Allow-list check:
   - existing active principal in `platform_admin_principals`, **or**
   - bootstrap `super_admin` when `telegram_user_id ∈ PLATFORM_ADMIN_SUPER_TELEGRAM_IDS` (env, not git).
7. Challenge → `approved` + `principal_id`.
8. Browser polls `GET /v1/admin/auth/challenge/{id}?browser_nonce=…`. On success Core atomically marks challenge `used` (`UPDATE … RETURNING`) then creates one `platform_admin_sessions` row, sets HttpOnly cookie (`PLATFORM_ADMIN_COOKIE_NAME`).
9. `/v1/admin/*` reads cookie, validates session hash server-side. Logout revokes session row.

## Security rules

- No passwords, no bearer token in JS, no Telegram bot token in browser.
- Roles are never taken from Telegram username, ref, URL, or request body.
- `super_admin` cross-tenant reads require server-resolved tenant + `admin_sensitive_view` audit row.
- Admin tables use RLS; only `admin_connection()` sets `app.admin_service=true`.

## Staging hosts

- `cabinet.staging.wwc.best` → tenant `whieda` (optional host context for admin routes).
- Internal confirm endpoint skips tenant middleware.
