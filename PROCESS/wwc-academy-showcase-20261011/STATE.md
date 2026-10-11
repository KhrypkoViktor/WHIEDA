# STATE — витрины Академии (11.10.2026)

## Где

- Core: worktree `D:\Projects\_worktrees\whieda-core-academy-showcase`, ветка
  `core/academy-showcase-20261011`. Влита fast-forward в `core/bot-ux-20261008`
  (сессия «Site (fork)» выпускает Core с неё). Новые коммиты — rebase на
  `origin/core/bot-ux-20261008` и сказать той сессии.
- Сайт wwc.best: worktree `D:\Projects\_worktrees\wwc-site-academy-showcase`, ветка
  `site/academy-showcase-20261011` (`4babfe1`, от развёрнутого `3a74816`), запушена.
- almira.by: `D:\Projects\ALMIRA\site_src\_patch38.py` — подготовлен, НЕ применён.

## На бою (11.10.2026)

- Core core=staging=`5dd5b40`. Миграция V29 в общей базе, APPLIED.log.
- Данные: `almira_showcase_data.sql` (actor `almira` = Александра Пармон, Telegram
  739736805; binding `almira-academy-bot` @ALMIRA_I_bot; витрина `almira`, hosts
  `almira.wwc.best`, `academy.almira.by`; `tenant_domains` academy.almira.by → whieda).
- env Core боя: `PLATFORM_ALMIRA_BOT_TOKEN`, `PLATFORM_ALMIRA_WEBHOOK_SECRET`
  (бэкап `.env.bak-20261011-almira`). Вебхук @ALMIRA_I_bot → Core, `message`+`callback_query`,
  старые 86 обновлений из группы заявок сброшены. lead.php (sendMessage) не затронут.
- Сайт wwc.best `4babfe1` (staging и бой).
- nginx origin (whieda-site): `X-WWC-Showcase-Host $host` в content-access и академии
  (бэкапы `/root/wwc.best.bak-20261011-academy`, `/root/wwc-core-uploads.conf.bak-20261011-academy`);
  новый vhost `/etc/nginx/sites-available/academy-almira` (только Академия, остальное → almira.by).
- Шлюз Core (docker-nginx-1, `/root/dify/docker/nginx/conf.d/n8n.conf`): map
  `$whieda_forwarded_host` — academy.almira.by проходит своим X-Forwarded-Host
  (кука только этого хоста); бэкап `/root/nginx-backup-20261011T071223Z-safe-install`.
- Московский прокси: `location ^~ /api/` — без кэша и буферизации (подписанные фото и
  видео Академии больше не кэшируются 7 дней и не пишутся на диск); блок :80
  academy.almira.by с webroot `/var/www/acme`; бэкап `/root/wwc-ru-proxy.bak-20261011`.
  Таймер `academy-almira-enable.timer` (5 мин): как только academy.almira.by
  резолвится в 217.26.28.71 — certbot, блок :443, reload, таймер выключается.
  Лог `/var/log/academy-almira-enable.log`.
- Cloudflare: `A edge.wwc.best → 217.26.28.71` (DNS only).
- Wildcard `*.wwc.best`: продление переведено с Spaceship на Cloudflare DNS-01
  (`/etc/letsencrypt/cloudflare.ini`, бэкап `/root/wwc-best-wildcard.conf.bak-20261011`),
  `certbot renew --dry-run` зелёный. Срок 30.11.2026 больше не угроза.

## Проверено

- Core: фокусные тесты и PG-интеграция зелёные (гейт release_core дважды).
- Бой: almira.wwc.best/academy/ — бренд ALMIRA, галочка согласия, «Получить ссылку» →
  t.me/ALMIRA_I_bot; wwc.best — как раньше (WWC, WHIEDA_Advisor_bot).
- academy.almira.by через origin напрямую: страницы, ассеты, API 200, кука без Domain.
- Вебхук ALMIRA: неверный секрет 403, сообщение из группы — молча.

## Не проверено

- Живой вход и бот в личке (нужен человек в Telegram): /start, вход, ключ, домашка.
- academy.almira.by снаружи — ждёт DNS.

## Дальше

1. DNS: Кирилл добавляет `academy CNAME edge.wwc.best` в hoster.by (или ключи API в
   `D:\Projects\ALMIRA\.secrets\hoster_api.env` + `migration/hoster_dns.py`). Сертификат и :443 —
   сами (таймер).
2. После HTTPS: в базе поменять порядок hosts (SQL внизу `almira_showcase_data.sql`);
   `python site_src/_patch38.py` → `build_site.py` → `build_prod.py` → `deploy_prod.sh`.
3. Саша: войти на academy.almira.by/academy/author/ (личный Telegram), создать «Композицию».
4. MAX: Академия в Max (вход, уведомления, бот ALMIRA в Max) — отдельный срез: ученик
   сейчас = telegram_user_id во всех таблицах Академии.
