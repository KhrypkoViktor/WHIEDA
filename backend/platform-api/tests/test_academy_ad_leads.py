"""Заявки с рекламы витрины (16.10.2026): разбор таблицы Meta, текст алерта, сопоставление с ботом."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
    # Заявка до открытия важнее чужой заявки через секунды после него (TheOldMan999, 17.10.2026).
    opened_at = datetime(2026, 10, 17, 6, 8, 40, tzinfo=timezone.utc)
    mine = replace(second, lead_id="mine", created=opened_at - timedelta(seconds=108))
    other = replace(organic, lead_id="other", created=opened_at + timedelta(seconds=10))
    assert match_lead([mine, other], username=None, name="x", opened_at=opened_at).lead_id == "mine"
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


def test_opened_text_hints_at_personal_link_when_row_not_found():
    """Строка не нашлась (19.10.2026) — администратору подсказка про личную ссылку из списка «не в боте»."""
    hint = "В таблице заявок не найдена — отправьте человеку личную ссылку из списка «не в боте»."
    assert opened_text(name="Мария", username=None, user_id=777, webinar=True, found=False) == (
        '✅ <b><a href="tg://user?id=777">Мария</a></b> (без @ — чат по имени) — в боте, ссылку на эфир получит здесь. '
        + hint
    )
    assert opened_text(name="Мария", username="masha", user_id=None, webinar=False, found=False).endswith(f"в боте. {hint}")
    assert "не найдена" not in opened_text(name="Мария", username=None, user_id=777, webinar=True)
    assert "не найдена" not in opened_text(name="Мария", username=None, user_id=777, webinar=True, found=True)


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


def test_single_first_name_or_clashing_nicks_do_not_match():
    """Наталья @Lliiss12345 открыла бота без заявки и сопоставилась с другой Натальей (@Nata_li_70) только
    по имени — та выпала бы из «Не в боте» (18.10.2026)."""
    other = replace(parse_leads(CSV)[0], lead_id="971", name="Наталья", username="Nata_li_70")
    opened_at = datetime(2026, 10, 18, 5, 34, tzinfo=timezone.utc)  # ни одной заявки за 5 минут до
    assert match_lead([other], username="Lliiss12345", name="Наталья", opened_at=opened_at) is None
    assert match_lead([other], username=None, name="Наталья", opened_at=opened_at) is None
    assert opened_for(other, [Opened(5274321005, "Lliiss12345", "Наталья", None, opened_at)]) is None
    # Имя с фамилией — признак сильнее «ника» из формы (Вита Василюк @vitavasilyuk777 написала в форме «Vita»).
    full = replace(other, name="Наталья Иванова")
    assert match_lead([full], username=None, name="Иванова Наталья").lead_id == "971"
    assert match_lead([full], username="Lliiss12345", name="Наталья Иванова").lead_id == "971"
    # По времени ники спорят — не он.
    timed = replace(other, created=opened_at - timedelta(seconds=90))
    assert match_lead([timed], username="Lliiss12345", name="Наталья", opened_at=opened_at) is None
    # …кроме формы за минуту до /start: в «ник» формы пишут ник Instagram (Маргарита Пятых «Sirni_pelmen»,
    # @Bonni_P через 13 с, 20.10.2026) — и строке, и флагу таблицы.
    rita = replace(other, name="Marharita Pyatykh", username="Sirni_pelmen", created=opened_at - timedelta(seconds=13))
    assert match_lead([rita], username="Bonni_P", name="®", opened_at=opened_at).lead_id == "971"
    assert opened_for(rita, [Opened(461610600, "Bonni_P", "®", None, opened_at)]) is not None
    # По времени (за 5 минут до /start) — да, если ники не спорят.
    near = replace(other, username=None, created=opened_at - timedelta(seconds=40))
    assert match_lead([near], username="Lliiss12345", name="Наталья", opened_at=opened_at).lead_id == "971"


# Вкладка формы 31.10 (21.10.2026): те же колонки, без ручных колонок Кирилла — он добавит их справа.
TAB31 = "\n".join([
    "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,form_name,is_organic,"
    "platform,что_интересует,учились_в_almira_раньше?,ваш_ник_в_telegram_(для_ссылки_на_эфир),полное_имя,"
    "номер_телефона,lead_status,,",
    "l:444,2026-10-24T03:10:00-05:00,ag:9,D1,as:9,D,c:9,Композиция,f:1126256416558363,Вебинар композиция 31.10,false,ig,"
    "курс_«игра_формы»,нет,,Ольга Курсова,p:+375291234567,CREATED,написал,оплатил 10к",
    "l:111,2026-10-16T08:40:11-05:00,ag:1,A3 — ошибки,as:2,A,c:3,Композиция,f:4,Вебинар композиция 24.10,false,ig,"
    "бесплатный_вебинар_24_октября,нет,t.me/kvetka_space,Анна Петрова,p:+375291112233,CREATED,,",
])


def test_both_tabs_read_merged_by_id_and_course_interest_goes_to_the_form_date(monkeypatch):
    import asyncio

    import httpx

    from app.academy import ad_leads
    from app.academy.webinars import is_paid, lead_webinar, webinars_config

    config = sheet_config({"leads_sheet": {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657",
                                           "gids": ["1983230657", "1990357724", "bad"]}})
    assert config["gids"] == ["1983230657", "1990357724"]
    assert sheet_config({"leads_sheet": {"id": config["id"], "gid": "5"}})["gids"] == ["5"]

    tabs = {"1983230657": CSV, "1990357724": TAB31}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=tabs[request.url.params["gid"]], headers={"content-type": "text/csv"})

    real = httpx.AsyncClient
    monkeypatch.setattr(ad_leads.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    leads = asyncio.run(ad_leads.fetch_leads(config))
    # Повтор id из второй вкладки не дублируется; по времени — новая заявка последней.
    assert [lead.lead_id for lead in leads] == ["111", "222", "333", "444"]
    olga = leads[-1]
    assert (olga.tab, leads[0].tab) == ("1990357724", "1983230657")
    assert olga.interest == "Курс «Игра формы»"
    webinars = webinars_config({"webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
    }})
    assert lead_webinar(olga, webinars) == "ig_web31"  # «Курс…» в форме 31.10 — дата по имени формы
    assert is_paid(olga) and olga.status == "написал | оплатил 10к"  # пометки Кирилла справа от lead_status
    text = alert_text(olga)
    assert text.startswith("🔔 Новая заявка · курс «Игра формы»\n💡 Выбран курс, а не дата эфира.")
    assert "по форме (Вебинар композиция 31.10)" in text and "🎯 Интерес: Курс «Игра формы»" in text
    assert "💡" not in alert_text(leads[0])  # вебинарная заявка — без подсказки
    assert ad_leads.sheet_url(config, olga.tab).endswith("gid=1990357724")


def test_anatomy_alert_says_series_and_drawing_base():
    from app.academy.ad_leads import lead_series, series_label, series_markers

    support = {"webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября"},
        "ig_an28": {"at": "2026-10-28T19:30:00+03:00", "date": "28 октября", "series": "anatomy", "form": "Анатом",
                    "label": "анатомия"},
    }}
    markers = series_markers(support)
    assert markers == [("анатом", "anatomy", "анатомия")]
    head = (
        "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,form_name,is_organic,"
        "platform,какой_эфир_удобнее?,есть_ли_у_вас_база_академического_рисунка?,полное_имя,номер_телефона,lead_status"
    )
    lead = parse_leads(head + "\nl:901,2026-10-23T10:00:00+03:00,,,,,,,f:9,Вебинар анатомия 28.10 и 7.11,false,ig,"
                              "\"вебинар_28_октября,_среда,_19:30\",немного,Анна Анатомова,p:+375291000901,CREATED")[0]
    assert lead_series(lead, markers) == "anatomy" and lead_series(parse_leads(CSV)[0], markers) == ""
    text = alert_text(lead, label=series_label("anatomy", markers))
    assert text.startswith("🔔 Новая заявка · анатомия · вебинар 28 октября, среда, 19:30")
    assert "✏️ База академического рисунка: Немного" in text
    assert "✏️" not in alert_text(parse_leads(CSV)[0])
