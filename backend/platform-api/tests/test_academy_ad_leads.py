"""Заявки с рекламы витрины (16.10.2026): разбор таблицы Meta, текст алерта, сопоставление с ботом."""

from __future__ import annotations

from datetime import datetime, timezone

from app.academy.ad_leads import (
    alert_text,
    clean_phone,
    clean_username,
    match_lead,
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
        "📞 +375291112233",
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
        "🔔 Новая заявка · Вебинар композиция 24.10", "✈️ ник не указан", "📣 органика", "🕓 16.10 в 17:10",
    ]


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
    assert opened_text(name="Анна Петрова", username="kvetka_space", webinar=True) == (
        "✅ <b>Анна Петрова</b> (@kvetka_space) — в боте, ссылку на эфир получит здесь."
    )
    assert opened_text(name="Мария", username=None, webinar=False) == "✅ <b>Мария</b> (ник не указан) — в боте."


def test_sheet_config_validates_ids():
    good = {"leads_sheet": {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657", "since": "2026-10-16T17:30:00+03:00"}}
    assert sheet_config(good)["gid"] == "1983230657"
    assert sheet_config({"leads_sheet": {"id": "../etc", "gid": "1"}}) is None
    assert sheet_config({}) is None and sheet_config(None) is None
