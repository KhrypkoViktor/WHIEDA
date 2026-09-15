# WWC Owner Cabinet — P0.1B: реальный Telegram-вход и одноразовый challenge

**Статус:** обязательная доработка перед тем, как называть кабинет доступным владельцу.  
**Основание:** review P0.1 от 2026-08-09.  
**Граница:** узкая интеграция входа; не Telegram cutover и не изменение работы советника.

## 1. Почему P0.1B нужен

В P0.1 `POST /v1/admin/auth/telegram-confirm` правильно защищён серверным секретом. Но действующий Telegram-маршрут не вызывает этот endpoint при `/start admin_login_<challenge>`.

Local smoke делает этот вызов напрямую от тестового процесса. Это доказывает API, но не доказывает путь «сканировал QR → Telegram → открыл кабинет».

Также текущий `poll_login_challenge` читает approved challenge без блокировки строки. Два параллельных poll-запроса потенциально могут создать две сессии до того, как один запрос отметит challenge использованным.

## 2. Результат

На испытательной среде живой Telegram-переход выполняет:

```text
кабинет создаёт challenge
→ пользователь открывает Telegram deep link
→ действующий входной Telegram-маршрут распознаёт только admin_login_ prefix
→ серверно вызывает confirm с секретом из env
→ кабинет получает одну HttpOnly-сессию
→ повторный poll не создаёт вторую сессию
```

## 3. Разрешённые изменения

- `backend/platform-api`;
- минимальный действующий Telegram ingress/legacy n8n только для распознавания `admin_login_` и server-to-server confirm;
- staging config/secrets и тесты;
- документация/runbook.

## 4. Запрещено

- Telegram cutover советника на Core;
- изменение SQL-ответов, Dify, обычных `/start`, onboarding и delivery заявок;
- передача confirm secret в браузер, QR, URL, логи или Git;
- production deploy без явной команды владельца;
- UI кабинета;
- изменение Google Sheets, рынков, цен или сервисных центров.

## 5. Точная логика

1. В действующем Telegram ingress первым делом распознаётся start parameter с префиксом `admin_login_`.
2. Для него ingress вызывает `POST /v1/admin/auth/telegram-confirm` только по внутреннему адресу/сети, передавая секрет из server env и фактический Telegram user ID update-а.
3. Пользователь получает нейтральный ответ: «Вход в кабинет подтверждён. Вернитесь в браузер.»
4. Любой другой `/start` сохраняет существующее поведение без изменения.
5. Ошибки `challenge_expired`, `admin_not_allowed`, `challenge_already_used` получают нейтральный ответ без раскрытия списка администраторов или деталей системы.
6. В `poll_login_challenge` использовать транзакционную блокировку строки (`FOR UPDATE`) либо один атомарный `UPDATE ... WHERE status='approved' AND used_at IS NULL ... RETURNING`, чтобы только один запрос создавал сессию.
7. Второй конкурентный poll получает `used`/controlled state и не создаёт вторую запись `platform_admin_sessions`.

## 6. Проверки

1. Реальный staging Telegram `/start admin_login_...` подтверждает challenge владельца.
2. Неизвестный Telegram user получает отказ; кабинетную сессию не создаёт.
3. Обычный `/start` советника проходит без регрессии.
4. Секрет confirm отсутствует в browser response, deep link, Telegram message и logs.
5. Два параллельных poll для одного approved challenge создают ровно одну admin session.
6. Просроченный и повторно использованный challenge не создают сессию.
7. Полный pytest и local smoke P0.1 остаются зелёными.
8. Staging smoke фиксирует фактический URL/API, Telegram update ID без персональных данных и результат browser cookie exchange.

## 7. Отчёт

Только четыре пункта:

1. Что изменено.
2. Где реально проверено: staging URL/API и Telegram-сценарий.
3. Что проверено фактически.
4. Что не проверено/ограничения.

Нельзя писать «Telegram auth готов», если confirm снова вызывался только тестовым скриптом, а не реальным staging Telegram ingress.
