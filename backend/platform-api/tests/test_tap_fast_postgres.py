"""Нажатие кнопки на настоящей PostgreSQL (04.11.2026): сколько транзакций на нажатие, «Пропустить» два шага
подряд, двойной тап по «Заказать», «печатает…» не роняет обработку.

Настоящий SQL под RLS роли приложения, настоящие обработчики кнопок; Bot API подменён (tests/tap_testkit.py).
Транзакция = подключение, взятое из пула; цифры «было» (до правки) — в отчёте PROCESS/wwc-funnel-bot-20261030/reports/
core-tap-fast.md, замер scripts/measure_tap_roundtrips.py."""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from tests import tap_testkit as kit
from tests.postgres_testkit import MIGRATIONS, temporary_database

TAP_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")  # колонки форума: reports_thread_id


@pytest.fixture
def tap_env(monkeypatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(kit.ADMIN))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(kit.OWNER))
    monkeypatch.setenv("TAP_BOT_TOKEN", kit.BOT_TOKEN)
    monkeypatch.setenv("TAP_WEBHOOK_SECRET", "tap-secret")
    monkeypatch.delenv("PLATFORM_TELEGRAM_CALLBACK_TYPING", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed(db) -> None:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        db.apply_migrations(conn, TAP_MIGRATIONS)
        conn.execute(kit.SEED)
        conn.execute("alter table tenant_bot_bindings add column if not exists bot_token_ref text, add column if not exists bot_username text")
        conn.execute(
            "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status) "
            "values (%s, 'whieda', 'env:TAP_WEBHOOK_SECRET', 'env:TAP_BOT_TOKEN', 'WHIEDA_Advisor_bot', 'active')",
            (kit.BINDING_ID,),
        )
        db.grant_api_role(conn)


def _admin(db, query: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


def _fresh() -> None:
    from app.telegram.sequencer import reset_chat_sequencer_for_tests
    from app.ttl_cache import clear_all_caches

    clear_all_caches()
    reset_chat_sequencer_for_tests()


async def _settle() -> None:
    from app.telegram import routes

    while routes._typing_tasks:
        await asyncio.gather(*list(routes._typing_tasks), return_exceptions=True)


async def _tap(data: str, *, update_id: int, message_id: int, user: int = kit.USER) -> None:
    from app.telegram.routes import _process_telegram_update

    update = kit.callback_update(data, update_id=update_id, message_id=message_id, user=user)
    await _process_telegram_update(kit.whieda_binding(), update, f"trace-{update_id}")
    await _settle()


def _texts(fake: kit.FakeTelegram) -> list[str]:
    return [str(call.payload.get("text") or "") for call in fake.sent("sendMessage")]


# ---- Сколько транзакций на нажатие ------------------------------------------------------------


@pytest.mark.integration
def test_transactions_per_tap_for_the_four_scenarios(tap_env, monkeypatch):
    fake = kit.FakeTelegram().install(monkeypatch)
    with temporary_database("whieda_tap_tx") as db:
        _seed(db)

        async def proof() -> None:
            from app.academy.showcases import showcase_default
            from app.support.service import FORUM_KIND_SERVICES, FORUM_KIND_SITE, get_forum
            from app.telegram.bindings import resolve_bot_binding_context

            async def measured(action) -> tuple[int, int]:
                counter.reset()
                await action()
                return counter.count, counter.queries

            async def warm() -> None:
                await resolve_bot_binding_context(kit.BINDING_ID)
                for kind in (FORUM_KIND_SERVICES, FORUM_KIND_SITE):
                    await get_forum("whieda", binding_id=kit.BINDING_ID, kind=kind)
                # Реквизиты услуг для ответа после «Заказать» лежат в настройках витрины (кэш 60 с).
                await showcase_default("whieda")

            with kit.TransactionCounter() as counter:
                # До обработки: привязка бота в вебхуке — одна транзакция, дальше из кэша.
                _fresh()
                assert (await measured(lambda: resolve_bot_binding_context(kit.BINDING_ID)))[0] == 1
                assert (await measured(lambda: resolve_bot_binding_context(kit.BINDING_ID)))[0] == 0

                # Обычный случай: кэши тёплые.
                _fresh()
                await warm()
                assert (await measured(lambda: _tap("svc:card:gemini", update_id=1, message_id=10)))[0] == 0
                assert (await measured(lambda: _tap("svc:order:gemini_6m", update_id=2, message_id=11)))[0] == 0
                assert (await measured(lambda: _tap("svc:confirm:gemini_6m", update_id=3, message_id=12)))[0] == 3
                assert (await measured(lambda: _tap("site:skip", update_id=4, message_id=13)))[0] == 1
                assert (await measured(lambda: _tap("site:skip", update_id=5, message_id=14)))[0] == 1

            # Первое нажатие после перезапуска (кэши пусты) — всё равно в пределах 5–6 транзакций на нажатие
            # вместе с привязкой бота. Заново поднимаем данные: заявка на сайт снова на шаге «фото».
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                conn.execute("update partner_site_requests set status = 'awaiting_photo'")
                conn.execute("update support_tickets set status = 'closed'")
            with kit.TransactionCounter() as counter:
                _fresh()
                cold_confirm = (await measured(lambda: _tap("svc:confirm:gemini_6m", update_id=6, message_id=22)))[0]
                _fresh()
                cold_skip = (await measured(lambda: _tap("site:skip", update_id=7, message_id=23)))[0]
            # «Заказать» на холодном кэше читает ещё и настройки витрины (реквизиты услуг, 09.11.2026): +1 к прежним 4.
            assert cold_confirm <= 5 and cold_skip <= 2

        db.run_with_app(proof)
    assert fake.calls  # Bot API подменён: в сеть ничего не ушло


# ---- «Пропустить» два шага подряд ---------------------------------------------------------------


@pytest.mark.integration
def test_skip_on_two_steps_in_a_row_passes_both_times(tap_env, monkeypatch):
    fake = kit.FakeTelegram().install(monkeypatch)
    with temporary_database("whieda_tap_skip") as db:
        _seed(db)

        async def proof() -> None:
            _fresh()
            # Шаг «фото» → «текст» → «контакты» за доли секунды: одна и та же callback_data, разные сообщения.
            await _tap("site:skip", update_id=1, message_id=100)
            assert _admin(db, "select status from partner_site_requests")[0][0] == "awaiting_text"
            await _tap("site:skip", update_id=2, message_id=101)
            assert _admin(db, "select status from partner_site_requests")[0][0] == "awaiting_contacts"
            texts = _texts(fake)
            assert any("Расскажите в паре предложений" in t for t in texts)  # вопрос шага «текст»
            assert any("Какие контакты показать" in t for t in texts)  # вопрос шага «контакты»
            assert fake.sent("answerCallbackQuery") != []
            assert all(call.payload == {"callback_query_id": call.payload["callback_query_id"]} for call in fake.sent("answerCallbackQuery"))

            # Повторный тап по той же кнопке того же сообщения — дубль: шаг не пропущен второй раз,
            # человек получил ответ на тап без текста.
            before = len(fake.sent("sendMessage"))
            answers_before = len(fake.sent("answerCallbackQuery"))
            await _tap("site:skip", update_id=3, message_id=101)
            assert _admin(db, "select status from partner_site_requests")[0][0] == "awaiting_contacts"
            assert len(fake.sent("sendMessage")) == before
            assert len(fake.sent("answerCallbackQuery")) == answers_before + 1

        db.run_with_app(proof)


@pytest.mark.integration
def test_skip_without_an_open_step_tells_the_person_and_keeps_the_actor(tap_env, monkeypatch):
    fake = kit.FakeTelegram().install(monkeypatch)
    stranger = 5550777
    with temporary_database("whieda_tap_skip_err") as db:
        _seed(db)

        async def proof() -> None:
            _fresh()
            await _tap("site:skip", update_id=1, message_id=5, user=stranger)
            assert any("Этот шаг пропустить нельзя" in t for t in _texts(fake))
            # Актор записан и после отказа — как было, когда актор и шаг шли двумя транзакциями.
            assert _admin(db, "select count(*) from lead_actors where telegram_user_id = %s", (stranger,))[0][0] == 1

        db.run_with_app(proof)


# ---- Двойной тап по «Заказать» -------------------------------------------------------------------


@pytest.mark.integration
def test_double_tap_on_confirm_makes_one_order(tap_env, monkeypatch):
    fake = kit.FakeTelegram().install(monkeypatch)
    # createForumTopic и отправки в Telegram занимают время — второй тап приходит, пока первый занят.
    fake.delay_methods.update({"createForumTopic": 0.15, "sendMessage": 0.05})
    with temporary_database("whieda_tap_double") as db:
        _seed(db)

        async def proof() -> None:
            from app.telegram.routes import _process_telegram_update

            _fresh()
            binding = kit.whieda_binding()
            first = kit.callback_update("svc:confirm:gemini_6m", update_id=1, message_id=300, query_id="tap-1")
            second = kit.callback_update("svc:confirm:gemini_6m", update_id=2, message_id=300, query_id="tap-2")
            await asyncio.gather(
                _process_telegram_update(binding, first, "trace-1"),
                _process_telegram_update(binding, second, "trace-2"),
            )
            await _settle()
            assert _admin(db, "select count(*) from support_tickets where user_telegram_user_id = %s", (kit.USER,))[0][0] == 1
            assert len(fake.sent("createForumTopic")) == 1
            texts = _texts(fake)
            assert sum("Заказ: Gemini Pro" in t for t in texts) == 1  # администратору — одно сообщение о заказе
            assert not any("снова нажал" in t for t in texts)  # повторной «шапки» нет
            assert sum("получили вашу заявку" in t for t in texts) == 1  # человеку — одно подтверждение
            # Оба нажатия получили ответ, оба без текста; «часики» сняты и у дубля.
            answered = sorted(call.payload["callback_query_id"] for call in fake.sent("answerCallbackQuery"))
            assert answered == ["tap-1", "tap-2"]
            assert all("text" not in call.payload for call in fake.sent("answerCallbackQuery"))
            # Дубль получил ответ сразу, а не после того, как первый тап дошёл до конца: подтверждение заявки уходит последним.
            order = [
                call.payload.get("callback_query_id") or str(call.payload.get("text") or "")
                for call in fake.calls
                if call.method in {"answerCallbackQuery", "sendMessage"}
            ]
            accepted_at = next(i for i, item in enumerate(order) if "получили вашу заявку" in item)
            assert order.index("tap-2") < accepted_at

        db.run_with_app(proof)


# ---- «Печатает…» ----------------------------------------------------------------------------------


@pytest.mark.integration
def test_typing_is_sent_and_a_telegram_failure_does_not_break_the_tap(tap_env, monkeypatch):
    fake = kit.FakeTelegram().install(monkeypatch)
    with temporary_database("whieda_tap_typing") as db:
        _seed(db)

        async def proof() -> None:
            _fresh()
            await _tap("svc:card:gemini", update_id=1, message_id=10)
            typing = fake.sent("sendChatAction")
            assert [call.payload for call in typing] == [{"chat_id": str(kit.USER), "action": "typing"}]
            assert fake.sent("answerCallbackQuery") and any("Gemini Pro" in t for t in _texts(fake))

            # Telegram не отвечает на «печатает…» — кнопка всё равно отрабатывает.
            fake.calls.clear()
            fake.fail_methods.add("sendChatAction")
            await _tap("svc:order:gemini_6m", update_id=2, message_id=11)
            assert len(fake.sent("sendChatAction")) == 1
            assert fake.sent("answerCallbackQuery") and any("Оформить заказ?" in t for t in _texts(fake))

            # И в базе: заявка на сайт и тот же «Пропустить» работают при сбое индикатора.
            await _tap("site:skip", update_id=3, message_id=12)
            assert _admin(db, "select status from partner_site_requests")[0][0] == "awaiting_text"

        db.run_with_app(proof)
