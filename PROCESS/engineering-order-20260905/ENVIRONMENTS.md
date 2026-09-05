# Карта сред WWC

Дата составления: 2026-09-05. Блок A инженерного пакета.

**Как читать колонку «evidence».** `verified` — значение получено командой,
которая названа тут же, с датой. `reported` — значение взято из отчёта,
переписки или артефакта, который сам по себе не доказывает соответствие.
`unverified` — не проверялось никак.

Онлайн-проверки (`nginx -T`, SSH) в блоке A не выполнялись. Значения с пометкой
`verified (perf-срез)` получены раньше, в рамках отдельного скоростного среза
того же дня, и приводятся с указанием метода — не как результат этого блока.
Список проверок для лида — в конце.

---

## 1. Домены и их обслуживание

| Домен | Host | Listener | Docroot | Evidence |
|---|---|---|---|---|
| `wwc.best`, `www.wwc.best` | `173.249.45.83` | `0.0.0.0:443`, `0.0.0.0:80`, `[::]:443`, `[::]:80` | `/var/www/whieda-sysarch` | verified (perf-срез) 2026-09-05: `ss -lntp`; docroot — из строк `/var/log/nginx/error.log` для `server: wwc.best` |
| `*.wwc.best` (13 партнёров) | `173.249.45.83` | тот же сокет `:443` | `/var/www/whieda-sysarch` | verified (perf-срез) 2026-09-05: `nginx -T \| grep server_name` показывает блок `server_name *.wwc.best` |
| `staging.wwc.best` | `173.249.45.83` | тот же сокет `:443` | `/var/www/staging-wwc-best` | verified 2026-09-05: распаковка артефакта в этот путь изменила отдаваемый `health.json` |
| `fedorov-staging.wwc.best` | `173.249.45.83` | тот же сокет `:443` | `/var/www/staging-wwc-best` (тот же) | verified 2026-09-05: `health.json` совпадает со `staging.wwc.best` побайтно |
| `admin-staging.wwc.best` | `173.249.45.83` | тот же сокет `:443` | `/var/www/admin-staging-wwc-best` | reported: путь встречается в `error.log` 2026-09-05, отдельно не подтверждён |
| `whieda.sysarch.pro` | `173.249.45.83` | `:443` | 301 на `wwc.best` | reported |
| `media.sysarch.pro` | `173.249.45.83` | `:443` | медиа-хост | reported |
| `mlm.sysarch.pro` | `173.249.45.83` | `:443` | `/var/www/mlm-sysarch` | reported: путь из `error.log` |

**Один nginx на все домены.** Прод, staging и партнёрские поддомены обслуживает
один процесс и один сокет `:443`. Отдельный каталог **не** делает staging
изолированным для инфраструктурных настроек — это уже стоило непреднамеренного
включения HTTP/2 на проде 2026-09-05 (откачено в тот же день).

- nginx: **1.24.0 (Ubuntu)** — verified (perf-срез) 2026-09-05, `nginx -v`.
  Версия важна: до 1.25.1 параметр `http2` относится к listen-сокету, а не к
  server-блоку.
- HTTP/2: **выключен везде** после отката — verified 2026-09-05,
  `openssl s_client -alpn h2,http/1.1` по трём хостам даёт `http/1.1`.
- AAAA-записей у `wwc.best`, `fedorov.wwc.best`, `staging.wwc.best` **нет** —
  verified 2026-09-05, запрос типа AAAA к 8.8.8.8. `[::]:443` слушается, но
  публично для этих имён не используется.

## 2. Код и артефакты

| Среда | Заявленный commit | Evidence | Чего это НЕ доказывает |
|---|---|---|---|
| production | `9044e52` | **reported**: маркер `git_commit` в отдаваемом `/health.json` 2026-09-05 | что содержимое docroot соответствует этому commit. `health.json` пишется при сборке; совпадение файлов по хешам не проверялось |
| staging | `e89d425` | verified 2026-09-05: артефакт собран и распакован в этот docroot в 11:19 UTC, `health.json` отражает сборку | то же ограничение: маркер, а не хеш дерева |
| `03_Website/wwc-best` (checkout) | `e1cd939`, ветка `fix/fedorov-runtime-context` | verified 2026-09-05: `git rev-parse HEAD` | ничего о том, что развёрнуто |
| task-worktree блока A | `bb603a9`, ветка `chore/engineering-order-20260905` | verified 2026-09-05: создан этим блоком от `origin/master` | |

`9044e52` существует в истории сайта и является предком `bb603a9`
(verified 2026-09-05: `git merge-base --is-ancestor`), между ними 5 commits.
Это делает `bb603a9` разумной базой пакета, но **выбор интеграционной базы
остаётся за лидом** — блок A её не назначает.

## 3. Runtime config

| Среда | Путь | Evidence |
|---|---|---|
| production | `/wwc-runtime-config.json` из docroot | reported |
| staging | alias на `/var/www/staging-wwc-best-config/wwc-runtime-config.staging.json`, `Cache-Control: no-store` | verified (perf-срез) 2026-09-05: чтение живого vhost `staging.wwc.best` |

## 4. API и данные

| Маршрут | Куда | Evidence |
|---|---|---|
| `/api/v1/public/ref/<code>` | `http://185.252.232.93:5678` | **reported** — из копии конфига в репозитории, которая устарела |
| `/api/v1/leads` | `http://185.252.232.93:5678/webhook/wwc-website-lead-v1` | reported, тот же источник |
| `/api/v1/content-access` | `https://sysarchn8n.duckdns.org/whieda-platform/api/v1/content-access` | reported, тот же источник |
| `/api/v1/theme-access/*` | **неизвестно** | в копии конфига такого `location` нет, но маршрут отвечает живьём (403, то есть доходит до приложения). Живой конфиг отличается от репозитория |
| staging `/api/` и `/api/v1/leads` | `http://127.0.0.1:18081` — тестовый sink, не боевой n8n | verified (perf-срез) 2026-09-05: чтение живого vhost |
| Базы данных | — | **unverified**, ни одна не проверялась |

**Важно про 403 и 401.** Код ответа не доказывает, что ответил Core: его может
вернуть nginx или промежуточный слой. Маршрут подтверждается живым конфигом и
`$upstream_addr` / `$upstream_status`, а не кодом.

**Устаревшая копия конфига.** `03_Website/wwc-best/wwc.best.nginx.conf` в
репозитории не соответствует живому конфигу. Её нельзя применять и нельзя
использовать как источник истины о маршрутах.

## 5. Что должен проверить лид перед любой инфраструктурной операцией

Блок A этого не делал намеренно. Каждая строка — одна команда на
`173.249.45.83`, все read-only:

1. `nginx -T | grep -n -A3 'location.*api/v1'` — полный список маршрутов
   `/api/v1/*` и их `proxy_pass`, включая отсутствующий в репозитории
   `theme-access`.
2. `nginx -T | grep -n 'root '` — фактические docroot всех vhost, чтобы
   подтвердить `admin-staging`, `media`, `mlm`, `whieda.sysarch.pro`.
3. Лог с `$upstream_addr`/`$upstream_status` по одному запросу к
   `/api/v1/theme-access/public` — доказать, кто именно отвечает.
4. Сверка прод-docroot с commit `9044e52`: хеши файлов дерева против сборки из
   этого commit. Пока не сделано, production SHA остаётся `reported`.
5. Решение: привести `wwc.best.nginx.conf` в репозитории в соответствие с
   живым конфигом (или пометить его как нерабочий черновик).
6. Инвентаризация БД: какие базы обслуживают Core и n8n, где они физически,
   кто их бэкапит. Сейчас `unverified` целиком.
