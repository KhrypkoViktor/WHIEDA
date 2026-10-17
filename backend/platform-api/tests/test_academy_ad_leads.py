"""Заявки с рекламы витрины (16.10.2026): разбор таблицы Meta, текст алерта, сопоставление с ботом."""

from __future__ import annotations

from datetime import datetime, timezone

from app.academy.ad_leads import (
    Opened,
    alert_text,
    clean_phone,
    clean_username,
    match_lead,
    opened_for,
    opened_text,
    parse_leads,
    sheet_config,
)

HEADER = (
    "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,form_name,is_organic,"
    "platform,что_интересует,учились_в_almira_раньше?,ваш_ник_в_telegram_(для_ссылки_на_эфир),полное_имя,"
    "номер_телефона,lead_status,Статус,Комментарий"
)
CSV = "\n".join([
    HEADER,
    "l:111,2026-10-16T08:40:11-05:00,ag:1,A3 — ошибки,as:2,A,c:3,Композиция,f:4,Вебинар композиция 24.10,false,ig,"
    "бесплатный_вебинар_24_октября,нет,t.me/kvetka_space,Анна Петрова,p:+375291112233,CREATED,,",
    "l:222,2026-10-16T08:54:16-05:00,ag:1,A3 — ошибки,as:2,A,c:3,Композиция,f:4,Вебинар композиция 24.10,false,fb,"
    "бесплатный_вебинар_24_октября,да_(скидка_15%),,Мария Иванова,p:+375 29 444-55-66,CREATED,,",
    "l:333,2026-10-16T09:10:00-05:00,,,,,,,f:4,Вебинар композиция 24.10,true,ig,,,,,,CREATED,,",
])


def test_parse_strips_meta_prefixes_and_reads_columns_by_question():
    first, second, organic = parse_leads(CSV)
    assert first.lead_id == "111" and first.phone == "+375291112233" and first.username == "kvetka_space"
    assert first.studied is False and first.interest == "Бесплатный вебинар 24 октября"
    assert first.source == "Instagram · A3 — ошибки"
    assert first.created == datetime(2026, 10, 16, 13, 40, 11, tzinfo=timezone.utc)
    assert second.phone == "+375294445566" and second.username is None and second.studied is True
    assert second.source == "Facebook · A3 — ошибки"
    assert organic.source == "органика" and organic.name == "" and organic.phone == ""


def test_alert_is_one_field_per_line_in_minsk_time():
    first, second, organic = parse_leads(CSV)
    assert alert_text(first) == "\n".join([
        "🔔 Новая заявка · вебинар 24 октября",
        "<b>Анна Петрова</b>",
        "⚠️ Бот не открыт — свяжитесь напрямую",
        "📞 +375291112233",
        '💬 <a href="https://wa.me/375291112233">WhatsApp</a> · <a href="https://t.me/+375291112233">Telegram по номеру</a>',
        "✈️ @kvetka_space",
        "🎓 Впервые в ALMIRA",
        "🎯 Интерес: Бесплатный вебинар 24 октября",
        "📣 Instagram · A3 — ошибки",
        "🕓 16.10 в 16:40",
    ])
    text = alert_text(second)
    assert "✈️ ник не указан" in text and "🎓 Уже учились в ALMIRA — скидка 15%" in text and "🕓 16.10 в 16:54" in text
    # Пустые поля пропускаются; заголовок — по имени формы.
    assert alert_text(organic).splitlines() == [
        "🔔 Новая заявка · Вебинар композиция 24.10", "⚠️ Бот не открыт — свяжитесь напрямую",
        "✈️ ник не указан", "📣 органика", "🕓 16.10 в 17:10",
    ]


def test_opened_lead_gets_chat_link_flag_and_nick_from_telegram():
    _, second, _ = parse_leads(CSV)
    opened = Opened(telegram_user_id=5373318757, username="maria_i", name="Мария", lead_id="222")
    lines = alert_text(second, opened).splitlines()
    assert lines[1:3] == [
        '<b><a href="tg://user?id=5373318757">Мария Иванова</a></b>',
        "✅ Бот открыт — ссылку на эфир получит там",
    ]
    # Ника в форме нет — берём из Telegram; прямые контакты остаются.
    assert "✈️ @maria_i" in lines and "📞 +375294445566" in lines


def test_direct_contact_links_survive_formatting():
    from app.telegram.delivery import format_telegram_html

    first, _, _ = parse_leads(CSV)
    rendered = format_telegram_html(alert_text(first))
    assert '<a href="https://wa.me/375291112233">WhatsApp</a>' in rendered
    assert '<a href="https://t.me/+375291112233">Telegram по номеру</a>' in rendered
    opened = Opened(telegram_user_id=301, username=None, name="Анна", lead_id=None)
    assert '<b><a href="tg://user?id=301">Анна Петрова</a></b>' in format_telegram_html(alert_text(first, opened))


def test_time_matches_when_telegram_name_differs():
    """«Svetik» открыла бота через 16 с после формы «Светлана Самохвал» — это она (17.10.2026)."""
    first, second, organic = parse_leads(CSV)  # 13:40:11, 13:54:16, 14:10:00 UTC
    at = datetime(2026, 10, 16, 13, 54, 40, tzinfo=timezone.utc)
    assert match_lead(parse_leads(CSV), username=None, name="Svetik", opened_at=at).lead_id == "222"
    # Больше OPEN_AFTER_FORM после заявки или раньше неё (с запасом на часы) — не она.
    late = datetime(2026, 10, 16, 14, 5, tzinfo=timezone.utc)
    assert match_lead(parse_leads(CSV), username=None, name="Svetik", opened_at=late) is None
    # Ник и имя важнее времени.
    assert match_lead(parse_leads(CSV), username="kvetka_space", name="x", opened_at=at).lead_id == "111"
    # Алерт: открытие без своей строки в пределах окна — её; со своей строкой — нет.
    free = Opened(9, None, "Svetik", None, at)
    claimed = Opened(8, None, "Svetik", "111", at)
    assert opened_for(second, [claimed, free]) is free
    assert opened_for(organic, [free]) is None


def test_opened_for_matches_row_then_nick_then_name():
    first, second, organic = parse_leads(CSV)
    by_row = Opened(1, None, "Кто-то", "222")
    by_nick = Opened(2, "KVETKA_SPACE", "Цветка", None)
    by_name = Opened(3, None, "Иванова  Мария", None)
    assert opened_for(second, [by_nick, by_row]) is by_row
    assert opened_for(first, [by_row, by_nick]) is by_nick
    assert opened_for(second, [by_name]) is by_name
    assert opened_for(first, [by_name]) is None
    # Пустое имя заявки ни с кем не сопоставляется.
    assert opened_for(organic, [Opened(4, None, "", None)]) is None


def test_username_and_phone_normalisation():
    assert clean_username("https://t.me/Anna_P") == "Anna_P"
    assert clean_username("@anna_p ") == "anna_p"
    assert clean_username("Анна") is None and clean_username("") is None
    assert clean_phone("p:+375 (29) 111-22-33") == "+375291112233"
    assert clean_phone("") == ""


def test_bot_open_matches_by_username_then_name():
    leads = parse_leads(CSV)
    assert match_lead(leads, username="KVETKA_space", name="x").lead_id == "111"
    assert match_lead(leads, username=None, name="Иванова Мария").lead_id == "222"
    assert match_lead(leads, username="nobody", name="Кто-то") is None
    assert opened_text(name="Анна Петрова", username="kvetka_space", user_id=301, webinar=True) == (
        '✅ <b><a href="tg://user?id=301">Анна Петрова</a></b> (@kvetka_space)'
        " — в боте, ссылку на эфир получит здесь."
    )
    # Оксана (16.10.2026): публичного ника нет ни в Telegram, ни в форме — чат по имени.
    assert opened_text(name="Мария", username=None, user_id=777, webinar=False) == (
        '✅ <b><a href="tg://user?id=777">Мария</a></b> (без @ — чат по имени) — в боте.'
    )
    assert opened_text(name="", username=None, user_id=None, webinar=False) == (
        "✅ <b>Без имени</b> (без @ — чат по имени) — в боте."
    )


def test_opened_link_survives_formatting_and_name_cannot_forge_it():
    from app.telegram.delivery import format_telegram_html

    rendered = format_telegram_html(
        opened_text(name='Оксана <a href="https://evil.example">', username=None, user_id=777, webinar=True)
    )
    assert rendered.startswith('✅ <b><a href="tg://user?id=777">Оксана')
    assert "evil.example" in rendered and '<a href="https://evil.example">' not in rendered


def test_sheet_config_validates_ids():
    good = {"leads_sheet": {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657", "since": "2026-10-16T17:30:00+03:00"}}
    assert sheet_config(good)["gid"] == "1983230657"
    assert sheet_config({"leads_sheet": {"id": "../etc", "gid": "1"}}) is None
    assert sheet_config({}) is None and sheet_config(None) is None
