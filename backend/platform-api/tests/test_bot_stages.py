"""Бот под три типа людей (04.11.2026): меню по статусу в Telegram и Max, `src_` и голый /start → гость, свободный текст
гостя не идёт советчику, подтверждение ведущего (идемпотентность, «Нет»), слово владельца, синяя кнопка «Меню» по scope.

Без базы и сети: база подменена на границах модулей, Telegram и Max — моками. Настоящий SQL, миграция V49 и сквозной
сценарий CRM → бот на живых строках — в test_bot_stage_postgres.py и test_bot_stage_crm_postgres.py.
"""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.bot_stages import confirm, menus, service
from app.bot_stages import max as stages_max
from app.bot_stages import telegram as stages_tg
from app.crm import stage_link
from app.funnel import guest, texts
from app.funnel.guest import guest_menu_reply, rows_to_max, rows_to_telegram
from app.max import processor as max_processor
from app.max.update_parser import MaxCallbackEvent, MaxEvent
from app.settings import get_settings
from app.telegram import referral_bonus
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramCallbackQuery, parse_telegram_message
from tests.test_bot_media import _through_processor, _update

USER = 5550001
OWNER = 688931415
HOST = 7001
UTC = timezone.utc
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
TUE = {"meeting_id": 11, "kind": "live", "title": "Живая встреча", "starts_at": datetime(2026, 11, 3, 16, 30, tzinfo=UTC), "status": "planned"}
THU = {"meeting_id": 12, "kind": "live", "title": "Живая встреча", "starts_at": datetime(2026, 11, 5, 16, 30, tzinfo=UTC), "status": "planned"}
OLGA_SITE = "https://olga.wwc.best"
MAX_TENANT = SimpleNamespace(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})


def labels(rows) -> list[str]:
    return [label for row in rows for label, _ in row]


def data(rows) -> list[str]:
    return [value for row in rows for _, value in row]


def tg_labels(markup: dict) -> list[str]:
    return [button["text"] for row in markup["inline_keyboard"] for button in row]


@asynccontextmanager
async def fake_connection(tenant_id: str):
    yield SimpleNamespace(tenant=tenant_id)


# ---- мир: статус человека и всё, что от него зависит, подменено -------------------------------------------


@pytest.fixture
def world(monkeypatch, stages_on, whieda_bot_binding):
    """Telegram: ``sent`` — все сообщения (чат, текст, клавиатура), ``calls`` — вызовы Bot API (setMyCommands …).
    Max: ``max_sent`` — (текст, кнопки). ``stage`` — статус, который «читает» бот."""
    w = SimpleNamespace(stage="guest", sent=[], calls=[], max_sent=[], view=(None, [TUE, THU]), inviter=None)

    async def send(*, chat_id, text, bot_token, reply_markup=None, **kwargs):
        w.sent.append((str(chat_id), text, reply_markup))
        return {"ok": True}

    async def call(method, payload, *, bot_token, timeout_sec=10.0):
        w.calls.append((method, payload))
        return {"ok": True}

    async def max_send(*, text, user_id=None, chat_id=None, buttons=None, **kwargs):
        w.max_sent.append((text, buttons))
        return {"ok": True}

    async def resolve(conn, tenant_id, channel, user_id):
        return w.stage

    async def person(tenant_id, channel, user_id):
        return w.stage

    async def card(tenant_id, owner):
        return SimpleNamespace(display_name=w.inviter) if w.inviter and owner else None

    for target in ("app.funnel.guest_bot.send_telegram_text", "app.bot_stages.telegram.send_telegram_text", "app.bot_stages.confirm.send_telegram_text"):
        monkeypatch.setattr(target, send)
    monkeypatch.setattr("app.telegram.referral_bonus._deliver", AsyncMock(side_effect=lambda chat_id, text, *, reply_markup=None: w.sent.append((str(chat_id), text, reply_markup))))
    for target in ("app.bot_stages.telegram._call_telegram", "app.bot_stages.confirm._call_telegram"):
        monkeypatch.setattr(target, call)
    for target in ("app.bot_stages.max.send_max_text", "app.max.processor.send_max_text", "app.funnel.guest_bot.send_max_text", "app.max.client.send_max_text"):
        monkeypatch.setattr(target, max_send)
    for target in ("app.funnel.guest_bot.resolve_stage", "app.funnel.guest.resolve_stage"):
        monkeypatch.setattr(target, resolve)
    for target in ("app.bot_stages.telegram.person_stage", "app.bot_stages.max.person_stage", "app.telegram.referral_bonus.person_stage"):
        monkeypatch.setattr(target, person)
    monkeypatch.setattr("app.funnel.guest_bot.tenant_connection", fake_connection)
    monkeypatch.setattr("app.funnel.guest.load_lead_by_channel_user", AsyncMock(return_value=None))
    monkeypatch.setattr("app.funnel.guest.inviter_actor_id", AsyncMock(side_effect=lambda *a, **kw: "igor-actor" if w.inviter else None))
    monkeypatch.setattr("app.funnel.guest_bot.try_relay_user_message", AsyncMock(return_value=None))
    monkeypatch.setattr("app.bot_stages.telegram.answer_callback_query", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr("app.funnel.guest_bot.load_guest_view", AsyncMock(side_effect=lambda *a, **kw: w.view))
    monkeypatch.setattr("app.funnel.guest_bot.owner_card", card)
    monkeypatch.setattr("app.bot_stages.telegram.example_site_url", AsyncMock(return_value=OLGA_SITE))
    monkeypatch.setattr("app.bot_stages.max.example_site_url", AsyncMock(return_value=OLGA_SITE))
    monkeypatch.setattr("app.bot_stages.max.tenant_connection", fake_connection)
    monkeypatch.setattr("app.bot_stages.max.own_site_url", AsyncMock(return_value="https://igor.wwc.best"))
    monkeypatch.setattr("app.crm.stage_link.check_partner_cards", AsyncMock(return_value=0))
    # кабинет партнёра WWC (его собирает referral_bonus): база и вход на сайт подменены
    dashboard = {"balance_wusd_minor": 1250, "site": {"url": "https://igor.wwc.best/", "subscription_status": "active", "paid_until": NOW, "days_remaining": 27}}
    monkeypatch.setattr("app.telegram.referral_bonus._actor_for_telegram", AsyncMock(return_value="actor"))
    monkeypatch.setattr("app.telegram.referral_bonus.get_or_create_invite_code", AsyncMock(return_value="c0de1234"))
    monkeypatch.setattr("app.telegram.referral_bonus.referral_dashboard", AsyncMock(return_value=dashboard))
    monkeypatch.setattr("app.telegram.referral_bonus.academy_button_rows", AsyncMock(return_value=[[{"text": "🎓 Академия", "callback_data": "acad:home"}]]))
    monkeypatch.setattr("app.telegram.referral_bonus.crm_button_rows", AsyncMock(return_value=[[{"text": "📒 WWC CRM", "url": "https://igor.wwc.best/crm/"}]]))
    monkeypatch.setattr("app.telegram.referral_bonus.with_site_login", AsyncMock(side_effect=lambda url, **kw: f"{url}#wwc-login=t"))
    monkeypatch.setattr("app.telegram.referral_bonus.list_referrals", AsyncMock(return_value={"items": [
        {"display_name": "Анна", "telegram_username": "anna", "status": "registered", "attributed_at": NOW}]}))
    with ExitStack() as stack:
        from app.telegram.bindings import binding_context_scope

        stack.enter_context(binding_context_scope(whieda_bot_binding))
        yield w


async def start_telegram(world, tenant, binding, text="/start", user=USER) -> dict[str, Any]:
    with ExitStack() as stack:
        _through_processor(stack)
        stack.enter_context(patch("app.telegram.processor.try_handle_wwc_service_text", AsyncMock(return_value=None)))
        stack.enter_context(patch("app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)))
        stack.enter_context(patch("app.telegram.processor.offer_marketing_consent", AsyncMock()))
        stack.enter_context(patch("app.telegram.processor.record_source_start", AsyncMock()))
        return await process_core_telegram_update(tenant, _update({"text": text}, user_id=user), "t1", binding=binding)


def max_event(kind="start", text="", user=9300, chat=777) -> MaxEvent:
    return MaxEvent(kind=kind, user_id=user, chat_id=chat, text=text, username=None, display_name="Инна", raw={})


@pytest.fixture
def max_world(world, monkeypatch):
    monkeypatch.setattr("app.max.processor.ensure_channel_actor", AsyncMock(return_value="max:whieda:9300"))
    monkeypatch.setattr("app.max.processor.register_start_source", AsyncMock(return_value=SimpleNamespace(first_seen=True, recorded=True)))
    return world


# ---- выключатель и статус --------------------------------------------------------------------------------


def test_the_three_menus_are_on_by_default_and_only_for_the_wwc_bot(monkeypatch):
    monkeypatch.delenv("PLATFORM_BOT_STAGES", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().platform_bot_stages is True
        assert service.stages_enabled("whieda") is True and service.stages_enabled() is True
        assert service.stages_enabled("nsp") is False  # боты других арендаторов этого модуля не видят
    finally:
        get_settings.cache_clear()


def test_the_switch_turns_the_three_menus_off(monkeypatch):
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    try:
        assert service.stages_enabled("whieda") is False
    finally:
        get_settings.cache_clear()


def test_the_owner_and_the_admins_are_always_a_wwc_partner(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", "5000")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "5001, 5002")
    get_settings.cache_clear()
    try:
        assert [service.is_owner_or_admin(user) for user in (5000, 5001, 5002, 5003, None)] == [True, True, True, False, False]
    finally:
        get_settings.cache_clear()


# ---- меню как данные -------------------------------------------------------------------------------------


def test_the_guest_menu_text_and_the_four_buttons(stages_on):
    reply = guest_menu_reply(None, registration=None, open_meetings=[TUE, THU], now=NOW)
    assert reply.text.startswith(
        "Вы в WWC: Whieda World Club — сообщество предпринимателей, которое выстроило систему развития МЛМ через интернет. С чего начать?"
    )
    # даты — как сейчас (две ближайшие встречи кнопками), под ними три кнопки меню
    assert labels(reply.rows) == [
        "📅 Записаться: вт 03.11 19:30", "📅 Записаться: чт 05.11 19:30", "▶ Показ системы", "🤝 Личный разбор", "✍️ Задать вопрос",
    ]
    assert data(reply.rows) == ["mreg:11", "mreg:12", "funnel:tour", "funnel:consult", "funnel:ask"]


def test_the_guest_menu_names_the_partner_before_the_wwc_line(stages_on):
    reply = guest_menu_reply("Игорь Волков", registration=None, open_meetings=[], now=NOW)
    assert reply.text.splitlines()[0] == "Вы пришли от Игорь Волков."
    assert reply.text.splitlines()[1].startswith("Вы в WWC: Whieda World Club")
    assert "Ближайших живых встреч пока нет" in reply.text
    assert data(reply.rows) == ["funnel:tour", "funnel:consult", "funnel:ask"]


def test_a_registered_guest_keeps_move_and_cancel(stages_on):
    registration = (SimpleNamespace(registration_id=7), THU)
    reply = guest_menu_reply(None, registration=registration, open_meetings=[], now=NOW)
    assert reply.rows[0] == [("Перенести", "mmove:7"), ("Отменить запись", "mcancel:7")]
    assert "Вы записаны на Живую встречу: чт 05.11 19:30 МСК." in reply.text


def test_with_the_switch_off_the_guest_menu_is_the_old_one():
    # прежние тесты идут с выключателем «выкл» (conftest): здесь то же, но явно — старые подписи и «Вы в WWC.»
    reply = guest_menu_reply(None, registration=None, open_meetings=[TUE], now=NOW)
    assert reply.text.startswith("Вы в WWC.\n\n")
    assert labels(reply.rows) == ["Записаться: вт 03.11 19:30", "Показ системы", "Личный разбор", "Задать вопрос"]


@pytest.mark.parametrize("channel", ["telegram", "max"])
def test_the_whieda_partner_menu(channel):
    reply = menus.partner_whieda_reply(channel=channel, example_url=OLGA_SITE)
    assert reply.text == "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди."
    assert labels(reply.rows) == [
        "1️⃣ Что дальше", "🌐 Пример сайта партнёра", "🛒 Заказать сайт WWC", "🎓 Курс «Запуск WWC»", "🤝 Личный разбор", "📅 Живая встреча",
        "✍️ Поддержка",
    ]
    values = data(reply.rows)
    assert values[:2] == ["stg:next", OLGA_SITE] and values[4:6] == ["funnel:consult", "funnel:meet"]
    if channel == "telegram":
        assert values[2:4] == ["site:create", "acad:home"] and values[6] == "svc:support:site"
    else:  # в Max заказ сайта и курс — кнопки-ссылки на сайт, поддержка — «Задать вопрос»
        assert values[2].startswith("https://wwc.best/start") and values[3].startswith("https://") and values[6] == "funnel:ask"
    joined = " ".join(labels(reply.rows))
    for gone in ("Баланс", "WWC$", "Gemini", "Калькулятор", "приглашение", "реферал"):
        assert gone not in joined and gone not in reply.text


def test_the_whieda_partner_menu_without_an_example_site_has_no_such_button():
    reply = menus.partner_whieda_reply(channel="telegram", example_url=None)
    assert "🌐 Пример сайта партнёра" not in labels(reply.rows) and len(reply.rows) == 5


def test_the_next_screen_lists_three_steps_with_their_buttons():
    reply = menus.partner_whieda_next_reply(channel="telegram", example_url=OLGA_SITE)
    assert [line[:2] for line in reply.text.splitlines() if line[:1].isdigit()] == ["1.", "2.", "3."]
    assert labels(reply.rows) == ["🌐 Пример сайта партнёра", "🛒 Заказать сайт WWC", "🎓 Курс «Запуск WWC»", "← В кабинет"]
    assert data(reply.rows)[-1] == "stg:menu"


def test_the_commands_of_the_blue_menu_button_by_stage():
    names = lambda stage: [item["command"] for item in menus.commands_for_stage(stage)]  # noqa: E731
    assert names("guest") == ["start", "support"]
    assert names("partner_whieda") == ["start", "site", "course", "support"]
    assert names("partner_wwc") == ["cabinet", "invite", "calculator", "max", "support", "wish"]
    assert names("something-else") == ["start", "support"]  # неизвестный статус — как гость, самый узкий
    # у бота без «Пожелания» и входа через Max их нет и в списке партнёра WWC
    assert [item["command"] for item in menus.commands_for_stage("partner_wwc", include_wish=False, include_max=False)] == [
        "cabinet", "invite", "calculator", "support",
    ]
    assert all(item["description"] for stage in ("guest", "partner_whieda", "partner_wwc") for item in menus.commands_for_stage(stage))


def test_the_wwc_partner_cabinet_has_the_new_names_and_no_old_ones():
    rows = referral_bonus._dashboard_rows(
        bot_username="bot", invite_code="c0de1234", site_url="https://igor.wwc.best/", has_site=True, minimal=False,
        academy_rows=[[{"text": "🎓 Академия", "callback_data": "acad:home"}], [{"text": "📒 WWC CRM", "url": "https://x/crm/"}]],
        stages=True,
    )
    flat = tg_labels(rows)
    assert flat == [
        "Мой сайт", "Продлить сайт", "🎓 Академия", "📒 WWC CRM", "🔗 Пригласить", "💰 История бонусов", "🤖 Товары и ответы",
        "Поддержка", "💡 Пожелание", "➕ Ещё",
    ]
    for gone in ("Посмотреть WWC", "Продлить платформу", "WWC Bot", "Скопировать приглашение", "Отправить приглашение", "Мои рефералы", "Gemini"):
        assert not any(gone in label for label in flat), gone
    callbacks = [button.get("callback_data") for row in rows["inline_keyboard"] for button in row]
    assert {"renew:start", "stg:invite", "referral:history", "nav:wwcbot", "stg:more"} <= set(callbacks)
    # сайта ещё нет: вместо «Продлить сайт» — заказ
    no_site = referral_bonus._dashboard_rows(
        bot_username="bot", invite_code="c0de1234", site_url="https://wwc.best/", has_site=False, minimal=False, stages=True,
    )
    assert tg_labels(no_site)[:2] == ["Сайт WWC", "🛒 Заказать сайт WWC"]


def test_with_the_switch_off_the_cabinet_keeps_the_old_buttons():
    rows = referral_bonus._dashboard_rows(
        bot_username="bot", invite_code="c0de1234", site_url="https://igor.wwc.best/", has_site=True, minimal=False,
    )
    flat = tg_labels(rows)
    assert "Продлить платформу" in flat and "🤖 WWC Bot" in flat and "Подключить Gemini Pro" in flat and "Мои рефералы" in flat


def test_the_cabinet_text_says_bonuses_are_a_discount_in_roubles():
    site = {"url": "https://igor.wwc.best/", "subscription_status": "active", "paid_until": NOW, "days_remaining": 27}
    text = referral_bonus.cabinet_text_partner_wwc(site, balance_minor=1250)
    assert "Бонусы: 12,50 WWC$ = 1 250 ₽ (скидка на услуги)" in text.splitlines()
    assert "Баланс" not in text and "t.me" not in text  # ссылка-приглашение теперь на экране «Пригласить»
    assert "Бонусы: 0 WWC$ = 0 ₽ (скидка на услуги)" in referral_bonus.cabinet_text_partner_wwc(None, balance_minor=0)


def test_the_more_screen_holds_the_services_and_the_calculator():
    reply = menus.more_reply()
    assert labels(reply.rows) == ["🧩 Сервисы", "🧮 Калькулятор", "← В кабинет"]
    assert data(reply.rows)[0] == "svc:card:gemini" and data(reply.rows)[1].startswith("https://") and data(reply.rows)[2] == "stg:menu"


def test_the_max_menu_of_a_wwc_partner_is_links_and_one_question_button():
    reply = menus.partner_wwc_max_reply(site_url="https://igor.wwc.best", telegram_bot_url="https://t.me/WHIEDA_Advisor_bot")
    assert labels(reply.rows) == ["Мой сайт", "Кабинет на сайте", "Открыть в Telegram", "✍️ Поддержка"]
    assert data(reply.rows) == ["https://igor.wwc.best", "https://wwc.best/me/", "https://t.me/WHIEDA_Advisor_bot", "funnel:ask"]
    assert "Мои заявки" in reply.text
    assert labels(menus.partner_wwc_max_reply(site_url=None, telegram_bot_url="https://t.me/x").rows)[0] == "Кабинет на сайте"


def test_every_new_text_the_funnel_owner_waits_for_is_marked():
    source = open(texts.__file__, encoding="utf-8").read()
    for name in ("STAGE_GUEST_INTRO", "STAGE_GUEST_FREE_TEXT", "STAGE_PW_INTRO", "STAGE_PW_NEXT_TEXT", "STAGE_PW_CONGRATS", "STAGE_CONFIRM_ASK",
                 "STAGE_BONUS_LINE", "STAGE_INVITE_HEAD", "STAGE_MORE_TEXT", "STAGE_BACK_TEXT", "STAGE_MAX_PWWC_TEXT"):
        before = source.split(f"\n{name} = ")[0].rsplit("\n", 4)[-4:]
        assert any("# текст: воронка" in line for line in before), name


def test_callback_data_of_the_confirm_buttons_fits_the_telegram_limit():
    token = stage_link.card_token("11111111-2222-3333-4444-555555555555")
    assert token == "11111111222233334444555555555555" and len(token) == 32
    assert stage_link.card_id_from_token(token) == "11111111-2222-3333-4444-555555555555"
    for prefix in (texts.STAGE_CONFIRM_CALLBACK_PREFIX, texts.STAGE_DECLINE_CALLBACK_PREFIX):
        assert len(f"{prefix}{token}".encode()) <= 64


# ---- выбор меню по статусу: 3 × Telegram ----------------------------------------------------------------


async def test_telegram_start_of_a_guest_shows_the_guest_menu_and_sets_the_guest_commands(world, whieda_tenant, whieda_bot_binding):
    world.stage = "guest"
    world.inviter = "Игорь Волков"
    result = await start_telegram(world, whieda_tenant, whieda_bot_binding)
    assert result["route"] == "guest_menu"
    chat, text, markup = world.sent[-1]
    assert chat == str(USER) and text.startswith("Вы пришли от Игорь Волков.\nВы в WWC: Whieda World Club")
    assert tg_labels(markup)[-3:] == ["▶ Показ системы", "🤝 Личный разбор", "✍️ Задать вопрос"]
    method, payload = world.calls[-1]
    assert method == "setMyCommands" and payload["scope"] == {"type": "chat", "chat_id": USER}
    assert [item["command"] for item in payload["commands"]] == ["start", "support"]


async def test_telegram_start_of_a_whieda_partner_shows_his_menu_and_commands(world, whieda_tenant, whieda_bot_binding):
    world.stage = "partner_whieda"
    result = await start_telegram(world, whieda_tenant, whieda_bot_binding)
    assert result["route"] == "stage_menu" and result["stage"] == "partner_whieda"
    _, text, markup = world.sent[-1]
    assert text == "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди."
    assert tg_labels(markup)[:3] == ["1️⃣ Что дальше", "🌐 Пример сайта партнёра", "🛒 Заказать сайт WWC"]
    assert markup["inline_keyboard"][1][0]["url"] == OLGA_SITE
    assert [item["command"] for item in world.calls[-1][1]["commands"]] == ["start", "site", "course", "support"]
    assert not any("Открываю личный кабинет" in line[1] for line in world.sent)


async def test_telegram_start_of_a_wwc_partner_shows_the_cabinet_and_the_six_commands(world, whieda_tenant, whieda_bot_binding):
    world.stage = "partner_wwc"
    result = await start_telegram(world, whieda_tenant, whieda_bot_binding)
    assert result["route"] == "referral"
    _, text, markup = world.sent[-1]
    assert text.startswith("Личный кабинет") and "Бонусы: 12,50 WWC$ = 1 250 ₽ (скидка на услуги)" in text
    assert tg_labels(markup)[:3] == ["Открыть кабинет", "Мой сайт", "Продлить сайт"]
    assert "🔗 Пригласить" in tg_labels(markup) and "➕ Ещё" in tg_labels(markup)
    assert [item["command"] for item in world.calls[-1][1]["commands"]] == ["cabinet", "invite", "calculator", "max", "support", "wish"]
    assert not any("Открываю личный кабинет" in line[1] for line in world.sent)


@pytest.mark.parametrize("stage", ["guest", "partner_whieda", "partner_wwc"])
async def test_telegram_cabinet_command_opens_the_menu_of_the_stage(world, whieda_tenant, whieda_bot_binding, stage):
    world.stage = stage
    # /cabinet идёт тем же show_referral_dashboard, что и /start
    result = await referral_bonus.show_referral_dashboard(
        whieda_tenant, telegram_user_id=USER, telegram_chat_id=USER, raw_update={}, trace_id="t"
    )
    assert result["ok"]
    _, text, _ = world.sent[-1]
    expected = {"guest": "Вы в WWC", "partner_whieda": "Вы партнёр WHIEDA", "partner_wwc": "Личный кабинет"}[stage]
    assert text.startswith(expected)


async def test_an_invite_request_of_a_guest_and_of_a_whieda_partner_shows_their_menu_not_a_link(world, whieda_tenant):
    for stage, expected in (("guest", "Вы в WWC"), ("partner_whieda", "Вы партнёр WHIEDA")):
        world.stage = stage
        await referral_bonus.show_invitation(whieda_tenant, telegram_user_id=USER, telegram_chat_id=USER, raw_update={}, trace_id="t")
        text = world.sent[-1][1]
        assert text.startswith(expected) and "t.me" not in text


async def test_the_invite_screen_of_a_wwc_partner_is_one_screen_with_link_buttons_and_referrals(world, whieda_tenant):
    world.stage = "partner_wwc"
    result = await referral_bonus.show_invitation(whieda_tenant, telegram_user_id=USER, telegram_chat_id=USER, raw_update={}, trace_id="t")
    assert result["route"] == "referral_invite"
    _, text, markup = world.sent[-1]
    assert text.splitlines()[0] == "🔗 Пригласить"
    assert "https://t.me/WHIEDA_Advisor_bot?start=ref_c0de1234" in text.splitlines()
    assert "Мои рефералы" in text and "Анна" in text
    assert tg_labels(markup) == ["Скопировать приглашение", "Отправить приглашение", "← В кабинет"]


# ---- выбор меню по статусу: 3 × Max ---------------------------------------------------------------------


async def test_max_start_of_a_guest_shows_the_guest_menu(max_world):
    max_world.stage = "guest"
    max_world.inviter = "Игорь Волков"
    result = await max_processor.handle_max_start(MAX_TENANT, max_event("start", ""), "t1")
    assert result["route"] == "max_stage_menu" and result["stage"] == "guest"
    text, buttons = max_world.max_sent[-1]
    assert text.startswith("Вы пришли от Игорь Волков.\nВы в WWC: Whieda World Club")
    assert [b["text"] for row in buttons for b in row][-3:] == ["▶ Показ системы", "🤝 Личный разбор", "✍️ Задать вопрос"]


async def test_max_start_of_a_whieda_partner_shows_his_menu_with_links(max_world):
    max_world.stage = "partner_whieda"
    result = await max_processor.handle_max_start(MAX_TENANT, max_event("start", ""), "t1")
    assert result["stage"] == "partner_whieda"
    text, buttons = max_world.max_sent[-1]
    assert text == "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди."
    flat = [b for row in buttons for b in row]
    assert [b["text"] for b in flat][:3] == ["1️⃣ Что дальше", "🌐 Пример сайта партнёра", "🛒 Заказать сайт WWC"]
    assert flat[1].get("url") == OLGA_SITE and flat[0].get("payload") == "stg:next"


async def test_max_start_of_a_wwc_partner_shows_text_and_links(max_world):
    max_world.stage = "partner_wwc"
    result = await max_processor.handle_max_start(MAX_TENANT, max_event("start", ""), "t1")
    assert result["stage"] == "partner_wwc"
    text, buttons = max_world.max_sent[-1]
    assert text.startswith("Вы партнёр WWC.") and "Мои заявки" in text
    assert [b["text"] for row in buttons for b in row] == ["Мой сайт", "Кабинет на сайте", "Открыть в Telegram", "✍️ Поддержка"]


@pytest.mark.parametrize(("stage", "head"), [("guest", "Вы в WWC"), ("partner_whieda", "Вы партнёр WHIEDA"), ("partner_wwc", "Вы партнёр WWC")])
async def test_the_menu_word_in_max_opens_the_menu_of_the_stage(max_world, stage, head):
    max_world.stage = stage
    result = await max_processor.handle_max_message(MAX_TENANT, max_event("message", "меню"), "t1")
    assert result["route"] == "max_stage_menu"
    assert max_world.max_sent[-1][0].startswith(head)


async def test_max_source_start_shows_the_guest_menu_and_not_the_old_welcome(max_world):
    from app.leads.start_sources import parse_source_start_token

    max_world.stage = "guest"
    result = await max_processor.handle_max_start(MAX_TENANT, max_event("start", "src_vk"), "t1")
    assert result["status"] == "source" and parse_source_start_token("src_vk").tag == "vk"
    assert max_world.max_sent[-1][0].startswith("Вы в WWC: Whieda World Club")
    assert "Мои заявки" not in max_world.max_sent[-1][0]


async def test_max_referral_start_greets_then_shows_the_menu_and_checks_waiting_cards(max_world):
    max_world.stage = "guest"
    inviter = SimpleNamespace(display_name="Ольга", site_url="https://olga.wwc.best")
    with patch("app.max.processor.accept_channel_referral_start", AsyncMock(return_value=SimpleNamespace(status="attributed", inviter_actor_id="olga"))), \
            patch("app.max.processor.inviter_card", AsyncMock(return_value=inviter)):
        await max_processor.handle_max_start(MAX_TENANT, max_event("start", "ref_abcdefgh"), "t1")
    greeting, menu = max_world.max_sent[-2][0], max_world.max_sent[-1][0]
    assert greeting.splitlines()[0] == "Приглашение сохранено." and "Сайт партнёра: https://olga.wwc.best" in greeting
    assert "Напишите вопрос по товару" not in greeting and "Мои заявки" not in greeting  # совет советчика ушёл из приветствия
    assert menu.startswith("Вы в WWC")
    stage_link.check_partner_cards.assert_awaited_once_with("whieda", "max", 9300)


async def test_the_max_stage_buttons_next_and_menu(max_world):
    max_world.stage = "partner_whieda"

    def press(payload: str) -> MaxCallbackEvent:
        return MaxCallbackEvent(callback_id="cb", payload=payload, user_id=9300, chat_id=777, message_id="m", chat_type="dialog",
                                username=None, display_name="Инна", raw={})

    assert max_processor.resolve_max_callback("stg:next")[1] is stages_max.max_stage_callback
    await stages_max.max_stage_callback(MAX_TENANT, press("stg:next"), "t")
    assert max_world.max_sent[-1][0].startswith("Что дальше — три шага")
    await stages_max.max_stage_callback(MAX_TENANT, press("stg:menu"), "t")
    assert max_world.max_sent[-1][0] == "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди."


# ---- src_ и голый /start → гость ------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/start", "/start src_vk", "/start src_vk_olgaref", "/start src_youtube", "меню", "С чего начать", "Привет"])
async def test_src_tag_and_bare_start_open_the_guest_menu_not_the_cabinet(world, whieda_tenant, whieda_bot_binding, text):
    world.stage = "guest"
    result = await start_telegram(world, whieda_tenant, whieda_bot_binding, text)
    assert result["route"] == "guest_menu"
    shown = world.sent[-1][1]
    assert "Вы в WWC: Whieda World Club" in shown
    for gone in ("Личный кабинет", "Баланс", "WWC$", "Gemini", "ref_"):
        assert gone not in shown
    assert not any("Открываю личный кабинет" in line[1] for line in world.sent)


async def test_a_src_tag_does_not_demote_a_partner(world, whieda_tenant, whieda_bot_binding):
    world.stage = "partner_wwc"
    result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "/start src_vk")
    assert result["route"] == "referral"  # партнёр по метке остаётся партнёром: меню — его
    assert world.sent[-1][1].startswith("Личный кабинет")


async def test_the_wwc_bot_word_of_a_guest_opens_the_guest_menu_not_the_advisor_panel(world, whieda_tenant, whieda_bot_binding):
    world.stage = "guest"
    with patch("app.telegram.processor.handle_newcomer_panel", AsyncMock(return_value={"route": "newcomer_panel"})) as panel:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "WWC Bot")
    assert result["route"] == "guest_menu"
    panel.assert_not_awaited()
    world.stage = "partner_wwc"
    with patch("app.telegram.processor.handle_newcomer_panel", AsyncMock(return_value={"route": "newcomer_panel"})) as panel:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "WWC Bot")
    assert result["route"] == "newcomer_panel"  # у партнёра «Товары и ответы» работает


# ---- свободный текст гостя не идёт советчику -------------------------------------------------------------


async def test_free_text_of_a_guest_gets_the_ask_button_and_never_reaches_the_advisor(world, whieda_tenant, whieda_bot_binding):
    world.stage = "guest"
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "когда ближайшая встреча?")
    assert result["route"] == "guest_free_text"
    advisor.assert_not_awaited()
    _, text, markup = world.sent[-1]
    assert text == "Нажмите «Задать вопрос» — передам Виктору."
    assert markup == {"inline_keyboard": [[{"text": "✍️ Задать вопрос", "callback_data": "funnel:ask"}]]}


@pytest.mark.parametrize("text", ["товары", "/products", "/match", "каталог", "/calculator"])
async def test_a_guest_does_not_open_the_catalogue_by_its_words(world, whieda_tenant, whieda_bot_binding, text):
    world.stage = "guest"
    with patch("app.telegram.processor.handle_navigation_text", AsyncMock(return_value={"route": "navigation_text"})) as navigation, \
            patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, text)
    assert result["route"] == "guest_free_text"
    navigation.assert_not_awaited()
    advisor.assert_not_awaited()


@pytest.mark.parametrize("stage", ["partner_whieda", "partner_wwc"])
async def test_partners_keep_the_advisor_and_the_catalogue(world, whieda_tenant, whieda_bot_binding, stage):
    world.stage = stage
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "расскажи про стельки")
    assert result["route"] == "advisor"
    advisor.assert_awaited_once()
    with patch("app.telegram.processor.handle_navigation_text", AsyncMock(return_value={"route": "navigation_text"})):
        assert (await start_telegram(world, whieda_tenant, whieda_bot_binding, "/products"))["route"] == "navigation_text"


async def test_a_database_failure_leaves_the_text_to_the_advisor(world, whieda_tenant, whieda_bot_binding, monkeypatch):
    monkeypatch.setattr("app.bot_stages.telegram.person_stage", AsyncMock(side_effect=RuntimeError("db down")))
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "расскажи про стельки")
    assert result["route"] == "advisor"
    advisor.assert_awaited_once()


async def test_free_text_of_a_guest_in_max_gets_the_ask_button(max_world):
    max_world.stage = "guest"
    with patch("app.max.processor.handle_structured_query", AsyncMock(return_value={"answer_mode": "x"})) as advisor:
        result = await max_processor.handle_max_message(MAX_TENANT, max_event("message", "когда встреча?"), "t1")
    assert result["route"] == "max_guest_free_text"
    advisor.assert_not_awaited()
    text, buttons = max_world.max_sent[-1]
    assert text == "Нажмите «Задать вопрос» — передам Виктору." and buttons[0][0]["text"] == "✍️ Задать вопрос"
    max_world.stage = "partner_wwc"
    with patch("app.max.processor.handle_structured_query", AsyncMock(return_value={"answer_mode": "x"})) as advisor:
        result = await max_processor.handle_max_message(MAX_TENANT, max_event("message", "стельки"), "t1")
    assert result["route"] == "max_message"
    advisor.assert_awaited_once()


# ---- с выключателем «выкл» бот отвечает как раньше -------------------------------------------------------


async def test_with_the_switch_off_a_bare_start_still_opens_the_old_cabinet(world, whieda_tenant, whieda_bot_binding, monkeypatch):
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    world.stage = "guest"
    with patch("app.telegram.processor.try_handle_guest_menu_text", AsyncMock(return_value=None)), \
            patch("app.telegram.processor._remove_legacy_reply_keyboard", AsyncMock()) as legacy:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding)
    assert result["route"] == "referral"
    legacy.assert_awaited_once()  # «Открываю личный кабинет.» — как раньше
    text = world.sent[-1][1]
    assert text.startswith("Личный кабинет") and "Баланс: 12,50 WWC$" in text and "t.me/WHIEDA_Advisor_bot?start=ref_c0de1234" in text
    assert world.calls == []  # синяя кнопка «Меню» не трогается


async def test_with_the_switch_off_free_text_still_goes_to_the_advisor(world, whieda_tenant, whieda_bot_binding, monkeypatch):
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    world.stage = "guest"
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "расскажи про стельки")
    assert result["route"] == "advisor"
    advisor.assert_awaited_once()


async def test_the_dead_main_menu_text_is_replaced_by_one_back_button(world, whieda_tenant):
    from app.telegram import catalog_browse

    with patch.object(catalog_browse, "send_telegram_text", AsyncMock(return_value={"ok": True})) as sent:
        await catalog_browse.handle_main_menu(whieda_tenant, USER)
    kwargs = sent.await_args.kwargs
    assert kwargs["text"] == "Вернуться в кабинет?"
    assert kwargs["reply_markup"] == {"inline_keyboard": [[{"text": "← В кабинет", "callback_data": "stg:menu"}]]}
    assert "Главное меню" not in kwargs["text"] and "📦" not in kwargs["text"]


async def test_with_the_switch_off_the_old_main_menu_text_stays(whieda_tenant, monkeypatch):
    from app.telegram import catalog_browse

    with patch.object(catalog_browse, "send_telegram_text", AsyncMock(return_value={"ok": True})) as sent, \
            patch.object(catalog_browse, "current_bot_binding", return_value=SimpleNamespace(bot_token="t")):
        await catalog_browse.handle_main_menu(whieda_tenant, USER)
    assert sent.await_args.kwargs["text"].startswith("Главное меню WHIEDA:")


# ---- кнопки stg:… ----------------------------------------------------------------------------------------


def callback(data: str, user: int = USER, message_id: int = 77) -> TelegramCallbackQuery:
    return TelegramCallbackQuery(
        chat_id=user, user_id=user, callback_query_id="cq", data=data, chat_type="private",
        raw={"callback_query": {"from": {"id": user, "first_name": "Игорь", "last_name": "Волков", "username": "igor_v"},
                                "message": {"message_id": message_id, "chat": {"id": user, "type": "private"}}}},
    )


async def test_the_next_the_more_and_the_back_buttons(world, whieda_tenant):
    world.stage = "partner_whieda"
    assert (await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:next"), trace_id="t"))["status"] == "next"
    assert world.sent[-1][1].startswith("Что дальше — три шага")
    result = await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:menu"), trace_id="t")
    assert result["status"] == "menu" and world.sent[-1][1].startswith("Вы партнёр WHIEDA")
    world.stage = "partner_wwc"
    assert (await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:more"), trace_id="t"))["status"] == "more"
    assert world.sent[-1][1] == "Ещё: подключение сервисов и калькулятор."
    assert (await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:invite"), trace_id="t"))["status"] == "invite"
    assert world.sent[-1][1].startswith("🔗 Пригласить")
    # устаревшая кнопка не по статусу: меню по статусу человека
    world.stage = "guest"
    assert (await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:more"), trace_id="t"))["status"] == "menu"
    assert world.sent[-1][1].startswith("Вы в WWC")


async def test_not_our_buttons_and_the_switch_off_leave_the_callback_alone(world, whieda_tenant, monkeypatch):
    assert await stages_tg.try_handle_stage_callback(whieda_tenant, callback("nav:menu"), trace_id="t") is None
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    assert await stages_tg.try_handle_stage_callback(whieda_tenant, callback("stg:menu"), trace_id="t") is None


# ---- «/site» и «/course» ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "command"), [("/site", "site"), ("/SITE", "site"), ("/course", "course"), ("/course@WHIEDA_Advisor_bot", "course"),
                                               ("/sites", None), ("/site now", None), ("site", None), (None, None)])
def test_menu_commands_of_a_whieda_partner(text, command):
    assert stages_tg.stage_command(text) == command


async def test_site_and_course_commands_run_the_button_scenarios(world, whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.processor.try_handle_site_request_callback", AsyncMock(return_value={"route": "site_request"})) as site:
        assert (await start_telegram(world, whieda_tenant, whieda_bot_binding, "/site"))["route"] == "site_request"
    pressed = site.await_args.args[1]
    assert (pressed.data, pressed.callback_query_id, pressed.user_id, pressed.chat_id) == ("site:create", "", USER, USER)
    with patch("app.telegram.processor.try_handle_academy_callback", AsyncMock(return_value={"route": "academy_home"})) as course:
        assert (await start_telegram(world, whieda_tenant, whieda_bot_binding, "/course"))["route"] == "academy_home"
    assert course.await_args.args[1].data == "acad:home"


async def test_the_site_command_follows_the_manual_mode_of_the_profile(world, whieda_tenant, whieda_bot_binding, monkeypatch):
    monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "minimal")
    get_settings.cache_clear()
    with patch("app.telegram.processor.try_handle_site_request_callback", AsyncMock()) as site, \
            patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
        result = await start_telegram(world, whieda_tenant, whieda_bot_binding, "/site")
    assert result["route"] == "manual_partner_operation"
    site.assert_not_awaited()
    assert "оформляются вручную" in deliver.await_args.args[1]


# ---- подтверждение ведущего: «Да», повтор, «Нет» -----------------------------------------------------------

CARD_ID = "11111111-2222-3333-4444-555555555555"
CARD = stage_link.CardInfo(contact_id=CARD_ID, name="Иван Петров", status="partner", host_user_id=HOST, persons=(("telegram", 8800),))


@pytest.fixture
def confirm_world(world, monkeypatch):
    world.promoted = {}
    world.card = CARD
    world.announced = []
    world.announce_ok = True

    async def promote(tenant_id, channel, user_id, stage):
        key = (channel, user_id)
        if key in world.promoted:
            return "already"
        world.promoted[key] = stage
        return "changed"

    async def announce(tenant_id, channel, user_id):
        world.announced.append((channel, user_id))
        return world.announce_ok

    monkeypatch.setattr("app.bot_stages.confirm.promote", promote)
    monkeypatch.setattr("app.bot_stages.confirm.announce_partner_whieda", announce)
    monkeypatch.setattr("app.bot_stages.confirm.stage_link.load_card_for_confirm", AsyncMock(side_effect=lambda tenant, contact: world.card))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    return world


def press_confirm(world, tenant, yes: bool, user: int = HOST):
    token = stage_link.card_token(CARD_ID)
    return stages_tg.try_handle_stage_callback(tenant, callback(f"stg:{'y' if yes else 'n'}:{token}", user=user), trace_id="t")


async def test_yes_makes_the_person_a_whieda_partner_tells_him_and_the_owner(confirm_world, whieda_tenant):
    result = await press_confirm(confirm_world, whieda_tenant, True)
    assert result["status"] == "confirmed" and result["delivered"] is True
    assert confirm_world.promoted == {("telegram", 8800): "partner_whieda"}
    assert confirm_world.announced == [("telegram", 8800)]
    chats = [(chat, text) for chat, text, _ in confirm_world.sent]
    assert (str(HOST), "Подтвердили: Иван Петров — партнёр WHIEDA. Ему отправлено поздравление и меню.") in chats
    assert (str(OWNER), "@igor_v подтвердил(а): Иван Петров — партнёр WHIEDA.") in chats
    # кнопки «Да» / «Нет» с вопроса сняты
    method, payload = confirm_world.calls[-1]
    assert method == "editMessageReplyMarkup" and payload["reply_markup"] == {"inline_keyboard": []} and payload["message_id"] == 77


async def test_a_second_yes_is_idempotent(confirm_world, whieda_tenant):
    await press_confirm(confirm_world, whieda_tenant, True)
    sent_before = len(confirm_world.sent)
    result = await press_confirm(confirm_world, whieda_tenant, True)
    assert result["status"] == "already"
    assert confirm_world.announced == [("telegram", 8800)]  # поздравление ушло один раз
    assert confirm_world.sent[sent_before:] == [(str(HOST), "Иван Петров уже партнёр WHIEDA — повторно писать не буду.", None)]


async def test_no_changes_nothing(confirm_world, whieda_tenant):
    result = await press_confirm(confirm_world, whieda_tenant, False)
    assert result["status"] == "declined"
    assert confirm_world.promoted == {} and confirm_world.announced == []
    assert confirm_world.sent[-1][1] == "Хорошо, статус Иван Петров не меняю."
    assert not any(chat == str(OWNER) for chat, _, _ in confirm_world.sent)


async def test_a_stranger_cannot_answer_for_the_card_but_the_owner_can(confirm_world, whieda_tenant):
    result = await press_confirm(confirm_world, whieda_tenant, True, user=9999)
    assert result["status"] == "forbidden" and confirm_world.promoted == {}
    assert confirm_world.sent[-1][1] == "Это вопрос другого партнёра."
    result = await press_confirm(confirm_world, whieda_tenant, True, user=OWNER)
    assert result["status"] == "confirmed" and confirm_world.promoted
    assert not any(chat == str(OWNER) and "подтвердил(а)" in text for chat, text, _ in confirm_world.sent)  # сам владелец — без уведомления себе


async def test_a_card_that_left_the_partner_stage_or_is_gone_changes_nothing(confirm_world, whieda_tenant):
    confirm_world.card = stage_link.CardInfo(CARD_ID, "Иван Петров", "deciding", HOST, (("telegram", 8800),))
    assert (await press_confirm(confirm_world, whieda_tenant, True))["status"] == "card_moved"
    assert confirm_world.promoted == {}
    confirm_world.card = None
    assert (await press_confirm(confirm_world, whieda_tenant, True))["status"] == "gone"
    confirm_world.card = stage_link.CardInfo(CARD_ID, "Иван Петров", "partner", HOST, ())
    assert (await press_confirm(confirm_world, whieda_tenant, True))["status"] == "no_bot"


async def test_a_person_who_closed_the_chat_is_still_confirmed_and_the_host_is_told(confirm_world, whieda_tenant):
    confirm_world.announce_ok = False
    result = await press_confirm(confirm_world, whieda_tenant, True)
    assert result["status"] == "confirmed" and result["delivered"] is False
    assert "Написать ему не удалось" in confirm_world.sent[-1][1] or any("Написать ему не удалось" in text for _, text, _ in confirm_world.sent)


async def test_both_messengers_of_the_person_are_confirmed_together(confirm_world, whieda_tenant):
    confirm_world.card = stage_link.CardInfo(CARD_ID, "Иван Петров", "partner", HOST, (("telegram", 8800), ("max", 9900)))
    result = await press_confirm(confirm_world, whieda_tenant, True)
    assert result["persons"] == 2
    assert confirm_world.announced == [("telegram", 8800), ("max", 9900)]


async def test_a_database_failure_on_yes_is_answered_and_nothing_is_announced(confirm_world, whieda_tenant, monkeypatch):
    monkeypatch.setattr("app.bot_stages.confirm.promote", AsyncMock(side_effect=RuntimeError("db down")))
    result = await press_confirm(confirm_world, whieda_tenant, True)
    assert result["status"] == "error" and result["ok"] is False
    assert confirm_world.announced == [] and confirm_world.sent[-1][1] == "Не получилось. Попробуйте ещё раз чуть позже."


# ---- слово владельца «партнёр @ник» -----------------------------------------------------------------------


@pytest.fixture
def owner_world(world, monkeypatch):
    world.found = [{"actor_id": "telegram:whieda:8800", "display_name": "@ivan_p", "telegram_user_id": 8800, "telegram_chat_id": "8800"}]
    world.promote = AsyncMock(return_value="changed")
    world.demote = AsyncMock(return_value="changed")
    world.announce = AsyncMock(return_value=True)
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    monkeypatch.setattr("app.bot_stages.telegram.find_telegram_person", AsyncMock(side_effect=lambda tenant, who: world.found))
    monkeypatch.setattr("app.bot_stages.telegram.promote", world.promote)
    monkeypatch.setattr("app.bot_stages.telegram.demote_to_guest", world.demote)
    monkeypatch.setattr("app.bot_stages.telegram.announce_partner_whieda", world.announce)
    return world


def owner_says(text: str, user: int = OWNER):
    return stages_tg.try_handle_owner_partner_word(
        TENANT_FOR_TESTS, parse_telegram_message(_update({"text": text}, user_id=user)), trace_id="t"
    )


TENANT_FOR_TESTS = SimpleNamespace(tenant_id="whieda")


@pytest.mark.parametrize("text", ["партнёр @ivan_p", "Партнер @ivan_p", "/партнёр @Ivan_P", "  партнёр   @ivan_p  ", "партнёр 88001"])
async def test_the_owner_word_makes_the_person_a_whieda_partner(owner_world, text):
    result = await owner_says(text)
    assert result["status"] == "promoted" and result["delivered"] is True
    owner_world.promote.assert_awaited_once_with("whieda", "telegram", 8800, "partner_whieda")
    owner_world.announce.assert_awaited_once_with("whieda", "telegram", 8800)
    assert owner_world.sent[-1][1] == "Готово: @ivan_p — партнёр WHIEDA. Ему отправлено поздравление и меню."


async def test_the_owner_word_twice_is_idempotent_and_does_not_greet_again(owner_world):
    owner_world.promote.return_value = "already"
    result = await owner_says("партнёр @ivan_p")
    assert result["status"] == "already"
    owner_world.announce.assert_not_awaited()
    assert owner_world.sent[-1][1] == "У @ivan_p уже статус партнёра WHIEDA."


async def test_the_owner_word_remove_returns_a_guest_and_resets_the_commands(owner_world):
    result = await owner_says("партнёр снять @ivan_p")
    assert result["status"] == "remove_changed"
    owner_world.demote.assert_awaited_once_with("whieda", "telegram", 8800)
    assert owner_world.sent[-1][1] == "Снял: @ivan_p — снова гость."
    assert owner_world.calls[-1][1]["scope"] == {"type": "chat", "chat_id": 8800}
    assert [item["command"] for item in owner_world.calls[-1][1]["commands"]] == ["start", "support"]
    owner_world.demote.return_value = "has_site"
    assert (await owner_says("партнёр убрать @ivan_p"))["status"] == "remove_has_site"
    assert "есть сайт WWC" in owner_world.sent[-1][1]


async def test_the_owner_word_for_a_person_the_bot_does_not_know(owner_world):
    owner_world.found = []
    assert (await owner_says("партнёр @nobody"))["status"] == "not_found"
    assert owner_world.sent[-1][1] == "Не нашёл человека в боте: @nobody. Пусть откроет бота и повторите."
    owner_world.found = [{"display_name": "A", "telegram_user_id": 1}, {"display_name": "B", "telegram_user_id": 2}]
    assert (await owner_says("партнёр @twins"))["status"] == "ambiguous"
    owner_world.promote.assert_not_awaited()


async def test_only_the_owner_and_only_this_word(owner_world):
    assert await owner_says("партнёр @ivan_p", user=9999) is None  # чужому слово не достаётся: текст пойдёт дальше
    owner_world.promote.assert_not_awaited()
    for text in ("партнёрская программа", "мой партнёр", "партнёрство", "оплата @ivan_p 3000 RUB"):
        assert await owner_says(text) is None, text
    assert (await owner_says("партнёр"))["status"] == "usage"
    assert (await owner_says("партнёр иван"))["status"] == "usage"
    assert owner_world.sent[-1][1].startswith("Формат: партнёр @ник")


async def test_the_owner_word_does_nothing_with_the_switch_off(owner_world, monkeypatch):
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    assert await owner_says("партнёр @ivan_p") is None


async def test_the_owner_word_goes_through_the_processor_before_the_advisor(owner_world, whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"route": "advisor"})) as advisor:
        result = await start_telegram(owner_world, whieda_tenant, whieda_bot_binding, "партнёр @ivan_p", user=OWNER)
    assert result["route"] == "stage_owner_word" and result["status"] == "promoted"
    advisor.assert_not_awaited()


# ---- синяя кнопка «Меню»: setMyCommands по scope ----------------------------------------------------------


async def test_set_my_commands_goes_to_the_chat_scope_for_each_stage(world):
    for stage, expected in (
        ("guest", ["start", "support"]),
        ("partner_whieda", ["start", "site", "course", "support"]),
        ("partner_wwc", ["cabinet", "invite", "calculator", "max", "support", "wish"]),
    ):
        assert await stages_tg.sync_chat_commands(4242, stage) is True
        method, payload = world.calls[-1]
        assert method == "setMyCommands"
        assert payload["scope"] == {"type": "chat", "chat_id": 4242}
        assert [item["command"] for item in payload["commands"]] == expected


async def test_a_failed_set_my_commands_does_not_break_the_reply(world, monkeypatch):
    monkeypatch.setattr("app.bot_stages.telegram._call_telegram", AsyncMock(side_effect=RuntimeError("telegram down")))
    assert await stages_tg.sync_chat_commands(4242, "guest") is False
    monkeypatch.setattr("app.bot_stages.telegram._call_telegram", AsyncMock(return_value={"ok": False}))
    assert await stages_tg.sync_chat_commands(4242, "guest") is False


async def test_no_commands_are_set_with_the_switch_off(world, monkeypatch):
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    assert await stages_tg.sync_chat_commands(4242, "guest") is False
    assert world.calls == []


async def test_announce_writes_the_congratulation_then_the_menu_and_resets_the_commands(world):
    assert await stages_tg.announce_partner_whieda("whieda", "telegram", 8800) is True
    texts_sent = [text for chat, text, _ in world.sent if chat == "8800"]
    assert texts_sent == [
        "Поздравляем — вы партнёр WHIEDA! Ниже ваше меню: сайт, курс, первые люди.",
        "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди.",
    ]
    assert world.calls[-1][1]["scope"] == {"type": "chat", "chat_id": 8800}
    assert [item["command"] for item in world.calls[-1][1]["commands"]] == ["start", "site", "course", "support"]
    assert await stages_tg.announce_partner_whieda("whieda", "max", 9900) is True
    assert [text for text, _ in world.max_sent][-2:] == texts_sent  # в Max те же два сообщения


async def test_announce_reports_a_closed_chat(world, monkeypatch):
    async def closed(*, chat_id, text, bot_token, reply_markup=None, **kwargs):
        raise RuntimeError("Forbidden: bot was blocked by the user")

    monkeypatch.setattr("app.bot_stages.telegram.send_telegram_text", closed)
    assert await stages_tg.announce_partner_whieda("whieda", "telegram", 8800) is False


# ---- CRM → бот: вопрос ведущему (без базы: границы подменены) ---------------------------------------------


class FakeConn:
    def __init__(self):
        self.queries = []

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


@pytest.fixture
def crm_world(monkeypatch, stages_on):
    w = SimpleNamespace(card=CARD, stage="guest", binding="whieda-advisor-bot", enqueued=[], created=True)

    async def load_card(conn, tenant_id, contact_id):
        return w.card

    async def resolve(conn, tenant_id, channel, user_id):
        return w.stage

    async def binding(conn, tenant_id):
        return w.binding

    async def enqueue(conn, **kwargs):
        w.enqueued.append(kwargs)
        return {"created": w.created}

    monkeypatch.setattr("app.crm.stage_link.load_card", load_card)
    monkeypatch.setattr("app.crm.stage_link.resolve_stage", resolve)
    monkeypatch.setattr("app.crm.stage_link.notify_binding_id", binding)
    monkeypatch.setattr("app.crm.stage_link.enqueue_guest_message", enqueue)
    return w


async def test_a_card_moved_to_partner_asks_the_host_with_yes_and_no(crm_world):
    assert await stage_link.ask_host(FakeConn(), "whieda", CARD_ID) == "queued"
    (message,) = crm_world.enqueued
    assert message["user_id"] == HOST and message["channel"] == "telegram" and message["binding_id"] == "whieda-advisor-bot"
    assert message["text"] == "Иван Петров стал партнёром WHIEDA — подтвердить? Он получит меню партнёра."
    token = stage_link.card_token(CARD_ID)
    assert message["rows"] == [[("Да", f"stg:y:{token}"), ("Нет", f"stg:n:{token}")]]
    assert message["idempotency_key"] == f"bot_stage_ask:{CARD_ID}"  # по карточке — один вопрос, сколько ни переводи


async def test_a_second_move_does_not_ask_again(crm_world):
    crm_world.created = False  # ключ идемпотентности уже в очереди
    assert await stage_link.ask_host(FakeConn(), "whieda", CARD_ID) == "duplicate"


@pytest.mark.parametrize(("change", "expected"), [
    ({"card": None}, "gone"),
    ({"card": stage_link.CardInfo(CARD_ID, "Иван", "deciding", HOST, (("telegram", 8800),))}, "gone"),
    ({"card": stage_link.CardInfo(CARD_ID, "Иван", "partner", HOST, ())}, "no_bot"),
    ({"stage": "partner_whieda"}, "already"),
    ({"stage": "partner_wwc"}, "already"),
    ({"card": stage_link.CardInfo(CARD_ID, "Иван", "partner", None, (("telegram", 8800),))}, "no_host"),
    ({"binding": None}, "no_binding"),
])
async def test_cards_that_must_not_ask(crm_world, change, expected):
    for key, value in change.items():
        setattr(crm_world, key, value)
    assert await stage_link.ask_host(FakeConn(), "whieda", CARD_ID) == expected
    assert crm_world.enqueued == []


async def test_the_hook_is_silent_with_the_switch_off_and_never_breaks_the_card(crm_world, monkeypatch):
    assert await stage_link.on_card_partner(FakeConn(), "whieda", CARD_ID) == "queued"
    monkeypatch.setattr("app.crm.stage_link.load_card", AsyncMock(side_effect=RuntimeError("db down")))
    assert await stage_link.on_card_partner(FakeConn(), "whieda", CARD_ID) == "error"
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "false")
    get_settings.cache_clear()
    crm_world.enqueued.clear()
    assert await stage_link.on_card_partner(FakeConn(), "whieda", CARD_ID) == "off"
    assert crm_world.enqueued == []


# ---- Max: клавиатура и канал не мешают друг другу ---------------------------------------------------------


def test_rows_become_telegram_and_max_keyboards_with_links_and_callbacks():
    reply = menus.partner_whieda_reply(channel="telegram", example_url=OLGA_SITE)
    telegram = rows_to_telegram(reply.rows)["inline_keyboard"]
    assert telegram[1] == [{"text": "🌐 Пример сайта партнёра", "url": OLGA_SITE}]
    assert telegram[0] == [{"text": "1️⃣ Что дальше", "callback_data": "stg:next"}]
    max_rows = rows_to_max(reply.rows)
    assert max_rows[1][0]["type"] == "link" and max_rows[0][0]["type"] == "callback"
