"""Дожим после вебинаров (Виктор, 02.11.2026) на настоящем PostgreSQL: сегменты came/missed (оба признака «был»),
кружки и копии рассылки, доступ к записи на N часов (не укорачивает, не трогает оплаченный и снятый), «Что остановило?»
с открытием обращения администратору."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.postgres_testkit import MIGRATIONS, temporary_database
from tests.test_academy_webinars_postgres import ADMIN, ANNA, BORIS, NO_LINK, PAID_CSV, SEED, SVETA, at

CIRCLE_A = "DQACAgIAAxkBAAIB" + "a" * 40
CIRCLE_B = "DQACAgIAAxkBAAIB" + "b" * 40
COPY_TO = 688931415
CODE_USER, OTHER_CODE, GUEST, OTHER_BUTTON, PURCHASED, LONG_GIFT, SHORT_GIFT, REVOKED = 3010, 3011, 3012, 3013, 3014, 3015, 3016, 3017
REPEATER = 3018  # был на эфире 24.10 и записан на повтор 31.10
SUPPORT_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")

SUPPORT = {
    **NO_LINK,
    "broadcast_copy": [COPY_TO],
    "webinars": {
        **NO_LINK["webinars"],
        "ig_web24": {**NO_LINK["webinars"]["ig_web24"], "url": "https://meet.google.com/abc-defg-hij"},
    },
    "webinar_warmup": [
        {
            "key": "c1", "after_min": 120, "text": "Спасибо, что были на эфире.", "video_note": CIRCLE_A, "audience": "came",
            "grant": {"course": "zapis-24", "hours": 48},
            "buttons": [[{"text": "Смотреть запись", "action": "course"}], [{"text": "Что остановило?", "action": "why"}]],
        },
        {
            "key": "m1", "after_min": 120, "text": "Вы не были, запись — по кнопке.", "video_note": CIRCLE_B, "audience": "missed",
            "buttons": [[{"text": "Что остановило?", "action": "why"}]],
        },
    ],
    "why": {"reasons": [{"code": "price", "text": "Дорого"}, {"code": "time", "text": "Нет времени"}]},
}

# Люди, которых нет в таблице заявок: вебинар — по метке /start; opened_at далеко от строк таблицы, чтобы их не связало по времени.
OPENS = f"""
insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username, name, tag, lead_id, opened_at) values
  ('whieda', 'almira', {CODE_USER}, null, 'Кодовик', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {OTHER_CODE}, null, 'Чужой код', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {GUEST}, null, 'Гость', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {OTHER_BUTTON}, null, 'Нажал на 31-е', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {PURCHASED}, null, 'Купила курс', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {LONG_GIFT}, null, 'Длинный подарок', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {SHORT_GIFT}, null, 'Короткий подарок', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {REVOKED}, null, 'Снятый доступ', 'ig_web24', null, '2026-10-21T10:00:00+03:00'),
  ('whieda', 'almira', {REPEATER}, null, 'Был и на повтор', 'ig_web31', null, '2026-10-21T10:00:00+03:00');
-- «Присоединиться»: Анна — на этом эфире, «Нажал на 31-е» — на другом, купившая — на этом.
update academy_ad_opens set joined = '{{"ig_web24": "2026-10-24T17:31:00+03:00"}}'::jsonb
  where telegram_user_id in ({ANNA}, {BORIS}, {PURCHASED});
update academy_ad_opens set joined = '{{"ig_web31": "2026-10-31T17:31:00+03:00"}}'::jsonb where telegram_user_id = {OTHER_BUTTON};
update academy_ad_opens set joined = '{{"ig_web24": "2026-10-24T17:40:00+03:00"}}'::jsonb where telegram_user_id = {REPEATER};
insert into academy_courses (tenant_id, slug, title, access_rule, author_actor_id, status, kind) values
  ('whieda', 'zapis-24', 'Запись эфира 24.10', 'purchase', 'almira', 'published', 'course'),
  ('whieda', 'podarok', 'Урок в подарок', 'purchase', 'almira', 'published', 'gift');
"""


@pytest.fixture(autouse=True)
def _forget_showcases():
    """Кэш витрин живёт дольше временной базы: следующие тесты (Академия, LMS) прочли бы из него чужую витрину."""
    yield
    from app.academy import showcases as sc
    from app.academy import webinars

    sc._reset_cache()
    webinars._last_confirm_fetch.clear()


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@pytest.mark.integration
def test_segments_circles_copies_and_hours_of_access(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    clock = at(24, 19, 31)  # через час после «после эфира +120 мин»
    with temporary_database("whieda_dozhim") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute("update academy_showcases set support = %s::jsonb where showcase_id = 'almira'",
                         (json.dumps(SUPPORT, ensure_ascii=False),))
            conn.execute(OPENS)
            rec = _rows(conn, "select course_id::text as id from academy_courses where slug = 'zapis-24'")[0]["id"]
            gift = _rows(conn, "select course_id::text as id from academy_courses where slug = 'podarok'")[0]["id"]
            access = (
                "insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref, expires_at, revoked_at)"
                " values ('whieda', %s::uuid, %s, %s, %s, %s, %s)"
            )
            soon, far = clock + timedelta(hours=1), clock + timedelta(days=10)
            # Признак «был» №2 — кодовое слово: метка эфира в payment_ref подарка (в одной строке их может быть несколько).
            conn.execute(access, (gift, CODE_USER, "gift", "code:ig_web31,ig_web24", clock + timedelta(days=6), None))
            conn.execute(access, (gift, OTHER_CODE, "gift", "code:ig_web31", clock + timedelta(days=6), None))
            # Курс записи уже открыт: оплатой, длинным подарком, коротким, снятым вручную.
            conn.execute(access, (rec, PURCHASED, "purchase", "order:77", soon, None))  # оплаченный, но со сроком
            conn.execute(access, (rec, LONG_GIFT, "gift", "code:ig_web24", far, None))
            conn.execute(access, (rec, SHORT_GIFT, "gift", "code:ig_web24", soon, None))
            conn.execute(access, (rec, REVOKED, "gift", "code:ig_web24", far, clock - timedelta(days=1)))
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            async def fake_fetch(config):
                return parse_leads(PAID_CSV)  # Борис оплатил

            monkeypatch.setattr(webinars, "fetch_leads", fake_fetch)
            webinars._last_confirm_fetch.clear()
            bindings = {"almira-academy-bot": object()}

            async def step(when: datetime) -> int:
                sc._reset_cache()
                return await webinars.webinar_step({"whieda"}, bindings, clock=when)

            async def sent() -> dict[str, dict]:
                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(conn, "select idempotency_key, payload from platform_outbox where event_type = %s", (webinars.EVENT,))
                return {r["idempotency_key"]: (r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])) for r in rows}

            async def access_of(uid: int, slug: str = "zapis-24") -> dict | None:
                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(
                        conn,
                        "select a.source, a.payment_ref, a.expires_at, a.revoked_at from academy_access a"
                        " join academy_courses c on c.course_id = a.course_id where a.telegram_user_id = %s and c.slug = %s",
                        (uid, slug),
                    )
                return rows[0] if rows else None

            assert await step(at(24, 19, 29)) == 0  # до «+120 мин» — ничего
            # 6 «были» + 3 «не были» + по копии каждого элемента (с кружком) Виктору.
            assert await step(clock) == 6 + 3 + 2
            assert await step(clock + timedelta(minutes=10)) == 0  # повтор — без дублей
            payloads = await sent()
            came = {int(k.rsplit(":", 1)[1]) for k in payloads if ":post_c1:" in k and ":copy:" not in k}
            missed = {int(k.rsplit(":", 1)[1]) for k in payloads if ":post_m1:" in k and ":copy:" not in k}
            # «Был»: кнопка «Присоединиться» (Анна, купившая) или кодовое слово среди меток (Кодовик, подарки); Борис
            # был, но оплатил — предложение после эфира ему не шлём. Метка другого эфира «был» не делает.
            assert came == {ANNA, CODE_USER, PURCHASED, LONG_GIFT, SHORT_GIFT, REVOKED}
            assert missed == {OTHER_CODE, GUEST, OTHER_BUTTON}
            assert not [k for k in payloads if k.endswith(f":{BORIS}") or k.endswith(f":{SVETA}")]

            first = payloads[f"webinar:almira:ig_web24:post_c1:{ANNA}"]
            assert first["video_note"] == CIRCLE_A and first["site_login_user_id"] == ANNA and "photos" not in first
            rows = first["reply_markup"]["inline_keyboard"]
            assert rows[0] == [{"text": "Смотреть запись", "url": "https://academy.almira.by/academy/?course=zapis-24"}]
            assert [r[0]["callback_data"] for r in rows[1:]] == ["wwhy:ig_web24:price", "wwhy:ig_web24:time"]
            second = payloads[f"webinar:almira:ig_web24:post_m1:{GUEST}"]
            assert second["video_note"] == CIRCLE_B and "site_login_user_id" not in second
            assert [r[0]["callback_data"] for r in second["reply_markup"]["inline_keyboard"]] == ["wwhy:ig_web24:price", "wwhy:ig_web24:time"]
            # Копия — как её видят люди: с кружком, а ссылка на курс входит на сайт под администратором.
            copy_c1 = payloads[f"webinar:almira:ig_web24:post_c1:copy:{COPY_TO}"]
            assert copy_c1["video_note"] == CIRCLE_A and copy_c1["site_login_user_id"] == COPY_TO and "получили 6" in copy_c1["text"]
            assert payloads[f"webinar:almira:ig_web24:post_m1:copy:{COPY_TO}"]["video_note"] == CIRCLE_B

            exp = clock + timedelta(hours=48)
            granted = await access_of(ANNA)
            assert (granted["source"], granted["payment_ref"], granted["expires_at"], granted["revoked_at"]) == (
                "gift", "grant:ig_web24:post_c1", exp, None)
            assert (await access_of(CODE_USER))["expires_at"] == exp  # у человека с кодом — свой курс-подарок, он нетронут
            code_gift = await access_of(CODE_USER, "podarok")
            assert code_gift["payment_ref"] == "code:ig_web31,ig_web24"
            # Оплаченный доступ не трогаем вовсе; длинный подарок не укорачиваем; короткий продлеваем, метка эфира цела.
            bought = await access_of(PURCHASED)
            assert (bought["source"], bought["payment_ref"], bought["expires_at"]) == ("purchase", "order:77", clock + timedelta(hours=1))
            long_gift = await access_of(LONG_GIFT)
            assert (long_gift["expires_at"], long_gift["payment_ref"]) == (clock + timedelta(days=10), "code:ig_web24")
            short_gift = await access_of(SHORT_GIFT)
            assert (short_gift["expires_at"], short_gift["payment_ref"], short_gift["source"]) == (exp, "code:ig_web24", "gift")
            # Снятый вручную доступ не воскрешаем.
            removed = await access_of(REVOKED)
            assert removed["revoked_at"] is not None and removed["expires_at"] == clock + timedelta(days=10)
            # Тем, кто «не были» (элемент без grant), доступ не выдавался; Борис (оплатил) и Светлана — тоже.
            for uid in (OTHER_CODE, GUEST, OTHER_BUTTON, BORIS, SVETA):
                assert await access_of(uid) is None
            # Подарок за кодовое слово остался как был: метки «был на эфире» не испорчены.
            assert (await access_of(OTHER_CODE, "podarok"))["payment_ref"] == "code:ig_web31"

            # Срок выдаётся один раз — когда сообщение встало в очередь, а не на каждом шаге.
            async with tenant_connection("whieda") as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update academy_access set expires_at = %s where telegram_user_id = %s and course_id = (select course_id from academy_courses where slug = 'zapis-24')",
                        (clock + timedelta(minutes=5), ANNA),
                    )
            assert await step(clock + timedelta(minutes=20)) == 0
            assert (await access_of(ANNA))["expires_at"] == clock + timedelta(minutes=5)

            # «Был» — по всей серии (02.11.2026): был 24.10, записан на повтор 31.10 и на нём не был — после 31.10 ему
            # «спасибо, что были», а не «вы не были». До своего эфира 31.10 он в рассылки 24.10 не попадал.
            assert not [k for k in payloads if k.endswith(f":{REPEATER}")]
            await step(at(31, 19, 31))
            after31 = await sent()
            assert f"webinar:almira:ig_web31:post_c1:{REPEATER}" in after31
            assert f"webinar:almira:ig_web31:post_m1:{REPEATER}" not in after31

        db.run_with_app(proof)


@pytest.mark.integration
def test_missing_course_holds_the_after_webinar_message_back_and_missing_segment_means_everyone(monkeypatch):
    """Слаг курса записи не нашёлся — сообщение с кнопкой «Смотреть запись» не уходит (а не уходит с мёртвой ссылкой);
    элемент без ``audience`` — всем записанным, как раньше."""
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    support = {
        **SUPPORT,
        "broadcast_copy": [],
        "webinar_warmup": [
            {"key": "c1", "after_min": 120, "text": "Запись.", "grant": {"course": "net-takogo", "hours": 48},
             "buttons": [[{"text": "Смотреть запись", "action": "course"}]]},
            {"key": "all", "after_min": 120, "text": "Всем записанным."},
        ],
    }
    with temporary_database("whieda_dozhim_nocourse") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute("update academy_showcases set support = %s::jsonb where showcase_id = 'almira'",
                         (json.dumps(support, ensure_ascii=False),))
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            async def fake_fetch(config):
                return parse_leads(PAID_CSV)

            monkeypatch.setattr(webinars, "fetch_leads", fake_fetch)
            webinars._last_confirm_fetch.clear()
            sc._reset_cache()
            assert await webinars.webinar_step({"whieda"}, {"almira-academy-bot": object()}, clock=at(24, 19, 31)) == 1  # только Анна
            async with tenant_connection("whieda") as conn:
                keys = {r["idempotency_key"] for r in await fetch_all(conn, "select idempotency_key from platform_outbox")}
                access = await fetch_all(conn, "select 1 from academy_access")
            assert keys == {f"webinar:almira:ig_web24:post_all:{ANNA}"}  # Борис оплатил, Светлана — на 31-е
            assert access == []

        db.run_with_app(proof)


@pytest.mark.integration
def test_unreadable_leads_sheet_holds_after_webinar_offers_back(monkeypatch):
    """Таблица заявок не открылась — не знаем, кто оплатил: предложение после эфира в этом шаге не уходит никому
    (оплатившим тоже), а в следующем шаге своего окна, когда таблица открылась, уходит как обычно (02.11.2026)."""
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    support = {**SUPPORT, "broadcast_copy": [], "webinar_warmup": [{"key": "all", "after_min": 120, "text": "Всем записанным."}]}
    with temporary_database("whieda_dozhim_nosheet") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute("update academy_showcases set support = %s::jsonb where showcase_id = 'almira'",
                         (json.dumps(support, ensure_ascii=False),))
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            state = {"down": True}

            async def flaky_fetch(config):
                if state["down"]:
                    raise RuntimeError("leads_sheet_not_csv")
                return parse_leads(PAID_CSV)

            monkeypatch.setattr(webinars, "fetch_leads", flaky_fetch)
            webinars._last_confirm_fetch.clear()
            sc._reset_cache()
            assert await webinars.webinar_step({"whieda"}, {"almira-academy-bot": object()}, clock=at(24, 19, 31)) == 0
            state["down"] = False
            sc._reset_cache()
            assert await webinars.webinar_step({"whieda"}, {"almira-academy-bot": object()}, clock=at(24, 19, 40)) == 1
            async with tenant_connection("whieda") as conn:
                keys = {r["idempotency_key"] for r in await fetch_all(conn, "select idempotency_key from platform_outbox")}
            assert keys == {f"webinar:almira:ig_web24:post_all:{ANNA}"}  # Борис оплатил — ему и теперь не уходит

        db.run_with_app(proof)


# ---- «Что остановило?» от нажатия до обращения администратору -----------------------------------------------

KIRILL, STUDENT = 7002, 9001
WHY_SUPPORT = {
    "admin_telegram_user_id": KIRILL,
    "webinars": {"ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""}},
    "why": {
        "reasons": [{"code": "price", "text": "Дорого"}, {"code": "think", "text": "Хочу подумать"}],
        "answers": {
            "price": {"text": "Напишите сюда, подберём вариант.", "label": "лист ожидания", "open": "admin",
                      "buttons": [[{"text": "Сайт", "url": "https://almira.by/x"}]]},
            "think": {"text": "Подумайте, мы на связи."},
        },
    },
}
WHY_SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('almira', 'whieda', 'Александра Пармон', '7001');
update lead_actors set telegram_user_id = 7001 where actor_id = 'almira';
alter table tenant_bot_bindings
  add column if not exists bot_token_ref text,
  add column if not exists bot_username text;
insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status)
values ('almira-academy-bot', 'whieda', 'env:ALMIRA_SECRET', 'env:ALMIRA_TOKEN', 'ALMIRA_I_bot', 'active');
"""
WHY_SHOWCASE = """
insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               links, telegram_binding_id, more_mode, support)
values ('whieda', 'almira', array['academy.almira.by'], 'almira', 'ALMIRA', 'Онлайн-академия ALMIRA',
        '{"home_url": "https://almira.by/", "contact_url": "https://t.me/alm_school"}',
        'almira-academy-bot', 'none', %s::jsonb)
"""


def _tap(data: str) -> dict:
    return {"update_id": 1, "callback_query": {
        "id": "cb", "data": data,
        "from": {"id": STUDENT, "is_bot": False, "first_name": "Мария", "username": "masha"},
        "message": {"message_id": 1, "chat": {"id": STUDENT, "type": "private"}},
    }}


def _text(text: str, message_id: int) -> dict:
    return {"update_id": message_id, "message": {
        "message_id": message_id, "text": text, "from": {"id": STUDENT, "is_bot": False, "first_name": "Мария", "username": "masha"},
        "chat": {"id": STUDENT, "type": "private"},
    }}


@pytest.mark.integration
def test_why_button_opens_the_admin_ticket_with_the_answer_as_the_only_message(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")
    with temporary_database("whieda_dozhim_why") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SUPPORT_MIGRATIONS)
            conn.execute(WHY_SEED)
            conn.execute(WHY_SHOWCASE, (json.dumps(WHY_SUPPORT, ensure_ascii=False),))
            db.grant_api_role(conn)

        sent: list[dict] = []

        async def fake_send(*, chat_id, text, bot_token, reply_markup=None, message_thread_id=None, **_):
            sent.append({"chat_id": int(chat_id), "text": text, "markup": reply_markup})
            return {"ok": True, "message_id": len(sent)}

        async def ok(**_):
            return {"ok": True}

        from app.telegram import showcase_bot as bot_module
        from app.telegram import showcase_support as ss

        for module in (bot_module, ss):
            monkeypatch.setattr(module, "send_telegram_text", fake_send)
            monkeypatch.setattr(module, "answer_callback_query", ok)

        def to(chat_id: int) -> list[dict]:
            return [row for row in sent if row["chat_id"] == chat_id]

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy.webinars import EVENT
            from app.db import fetch_all, tenant_connection
            from app.support.service import get_open_ticket_for_user
            from app.telegram.bindings import BotBindingContext, binding_context_scope
            from app.telegram.showcase_bot import process_showcase_bot_update
            from app.tenancy import TenantContext

            sc._reset_cache()
            showcase = await sc.showcase_for_host("whieda", "academy.almira.by")
            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
            binding = BotBindingContext(
                binding_id="almira-academy-bot", tenant=tenant, bot_token_ref="env:X", webhook_secret_ref="env:Y",
                bot_username="ALMIRA_I_bot", status="active", processing_mode="core", bot_token="t", webhook_secret="s",
                showcase_id="almira",
            )

            async def bot(update: dict) -> dict:
                with binding_context_scope(binding):
                    return await process_showcase_bot_update(tenant, update, "trace", showcase)

            async def alerts() -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(conn, "select idempotency_key, payload from platform_outbox where event_type = %s", (EVENT,))
                return [{"key": r["idempotency_key"], **(r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]))} for r in rows]

            # «Дорого» с open=admin: человеку одно сообщение — ответ с кнопками; обращение открыто, администратору — шапка и алерт.
            result = await bot(_tap("wwhy:ig_web24:price"))
            assert (result["route"], result["known"], result["admin_alert"]) == ("showcase_why", True, True)
            assert [row["text"] for row in to(STUDENT)] == ["Напишите сюда, подберём вариант."]
            assert to(STUDENT)[0]["markup"] == {"inline_keyboard": [[{"text": "Сайт", "url": "https://almira.by/x"}]]}
            ticket = await get_open_ticket_for_user("whieda", user_telegram_user_id=STUDENT, channel_code="academy:admin")
            assert ticket is not None and int(ticket["admin_telegram_user_id"]) == KIRILL
            assert to(KIRILL)[0]["text"].startswith("#A-1 · Мария (@masha) · администратору")
            [alert] = await alerts()
            assert alert["key"] == f"wwhy:almira:ig_web24:price:{STUDENT}" and alert["chat_id"] == str(KIRILL)
            assert alert["text"].startswith(f'❓ <b>Что остановило</b>: «Дорого» — <b><a href="tg://user?id={STUDENT}">Мария</a></b> (@masha)')
            assert alert["text"].endswith(" · композиция · после вебинара 24 октября · 📝 лист ожидания")

            # Следующее сообщение человека уходит администратору — как после кнопки «Написать администратору».
            relayed = await bot(_text("А рассрочка есть?", 5))
            assert relayed["route"] == "showcase_support_relay" and relayed["direction"] == "user_to_admin"
            assert to(KIRILL)[-1]["text"] == "#A-1 · Мария (@masha)\nА рассрочка есть?"

            # Повторное нажатие: ответ снова (снова одним сообщением), второго алерта и второго обращения нет.
            before = len(to(STUDENT))
            again = await bot(_tap("wwhy:ig_web24:price"))
            assert again["admin_alert"] is False
            assert [row["text"] for row in to(STUDENT)][before:] == ["Напишите сюда, подберём вариант."]
            assert len(await alerts()) == 1
            assert len([row for row in to(KIRILL) if "· администратору" in row["text"].splitlines()[0]]) == 1  # обращение одно, шапка одна

            # Причина без «open»: обычный ответ, обращение не открывается, алерт — без пометки.
            await bot(_tap("wwhy:ig_web24:think"))
            assert to(STUDENT)[-1]["text"] == "Подумайте, мы на связи."
            think = [a for a in await alerts() if a["key"].endswith(f":think:{STUDENT}")][0]
            assert "📝" not in think["text"] and "«Хочу подумать»" in think["text"]
            # Неизвестная причина — молча.
            count = len(sent)
            assert (await bot(_tap("wwhy:ig_web24:nope")))["known"] is False
            assert len(sent) == count

        db.run_with_app(proof)
