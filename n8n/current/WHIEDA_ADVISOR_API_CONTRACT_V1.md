# WHIEDA Advisor API Contract V1

Статус: backend contract. Сайт не меняется этим документом.

## Единый вход

`POST /webhook/whieda-advisor-api-v1`

Текущий Telegram webhook остаётся внутренней транспортной обёрткой и передаёт нормализованный запрос в тот же structured-конвейер.

## Вход

```json
{
  "tenant": "whieda",
  "ref": "optional-ref-code",
  "session": "browser-or-telegram-stable-session-id",
  "question": "Сколько стоит активатор?",
  "sku": "M015-00",
  "slug": "aktivator-kletok",
  "page_url": "https://example/page",
  "country": "BY",
  "language": "ru",
  "surface": "website"
}
```

Правила:

- `tenant`, `session`, `question` обязательны;
- `sku` и `slug` необязательны, но помогают точному follow-up;
- `ref` фиксируется как `first_ref`, если у сессии его ещё нет;
- запросы сайта используют тот же Postgres structured runtime, что и Telegram;
- Google Sheets и Dify не вызываются для цены, PV, карточки, фото, видео, PDF, сравнения, greeting и capability.

## Выход

```json
{
  "ok": true,
  "answer_text": "...",
  "answer_mode": "structured_price",
  "route": "structured",
  "product": {
    "sku": "M015-00",
    "canonical_name": "Активатор клеток"
  },
  "media": {
    "photo_url": "https://...",
    "videos": [],
    "documents": []
  },
  "clarifications": [],
  "sources": [],
  "context": {
    "last_product_sku": "M015-00"
  },
  "error_id": null
}
```

Ошибочный ответ:

```json
{
  "ok": false,
  "answer_text": "Сейчас не удалось обработать запрос. Попробуйте ещё раз.",
  "answer_mode": "error",
  "route": "error",
  "product": null,
  "media": {"photo_url": null, "videos": [], "documents": []},
  "clarifications": [],
  "sources": [],
  "context": {},
  "error_id": "uuid-or-execution-id"
}
```

## Режимы ответа

- `structured_card`, `structured_price`, `structured_media`, `structured_comparison`, `structured_business`;
- `clarification`;
- `deep_internal_library` только для явного глубокого запроса;
- `fallback`;
- `error`.

## Права и персонализация

- access: `candidate`, `approved`, `blocked`;
- `candidate` и `approved` получают обычные ответы; `blocked` получает только сообщение об отключении;
- персонализация применяется только при явном включении профиля;
- отключение тарифа отключает персонализацию и расширенные функции, но не ломает `ref` и старую ссылку.

## Граница сайта

Сайт использует этот контракт как клиент. Он не знает структуру таблиц, правила алиасов, SQL и Dify.
