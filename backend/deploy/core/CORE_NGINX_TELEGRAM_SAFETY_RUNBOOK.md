# Core nginx and Telegram webhook safety

## Incident 2026-09-12

`@WHIEDA_Advisor_bot` stopped answering. Telegram accumulated five updates and
reported `Connection refused`. The application containers were healthy, but the
shared container `docker-nginx-1` was restarting continuously.

Root cause: `/root/dify/docker/nginx/conf.d/nsp-staging.conf` referenced the absent
Docker upstream `whieda-shared-staging-api`. nginx resolves upstream names while
loading configuration. One invalid NSP staging vhost prevented the shared gateway
from starting, including the production WHIEDA Telegram webhook.

Recovery moved the broken file to `conf.d/disabled/`, restarted the gateway once,
verified external Core health 200, and confirmed that Telegram pending updates
dropped from five to zero.

## Why this could repeat

Staging and production have separate application containers but share one nginx
process and one `conf.d/*.conf` directory. They are isolated by content, not by
failure domain. Any syntactically valid config with an unavailable upstream can
stop the shared gateway after a restart.

## Mandatory rule

Never copy a file directly into `/root/dify/docker/nginx/conf.d` and never restart
`docker-nginx-1` for a normal configuration change. Use:

```bash
/opt/whieda-core/safe_install_dify_nginx_conf.sh /path/to/candidate.conf name.conf
```

The script backs up the previous file, validates the full live configuration,
reloads without stopping the old worker, checks external Core health and checks
the production Telegram webhook. Any failed check restores the previous file.

## Required checks after gateway changes

```bash
docker inspect -f '{{.State.Status}} restart={{.RestartCount}}' docker-nginx-1
docker exec docker-nginx-1 nginx -t
curl -fsS https://sysarchn8n.duckdns.org/whieda-platform/health/live
docker exec core-api-1 python scripts/check_telegram_webhook_health.py \
  --binding-id whieda-advisor-bot \
  --expected-url-fragment /whieda-platform/v1/telegram/whieda-advisor-bot/webhook
```

Healthy means: nginx is running, `nginx -t` succeeds, Core returns 200, webhook is
configured, and pending updates do not exceed the threshold. Tokens must never be
printed or pasted into reports.

## Structural fix

Move staging vhosts to a separate nginx container or at least a separate listener
and compose project. Until that is done, every staging gateway change is treated
as a production-risk change and must use the safe installer above.
