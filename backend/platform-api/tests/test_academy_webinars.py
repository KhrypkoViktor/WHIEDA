"""Напоминания о вебинарах витрины (18.10.2026): настройка, тексты, расписание, список «не в боте»."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.academy.ad_leads import parse_leads
from app.academy.webinars import (
    Webinar,
    _link_markup,
    call_list_texts,
    due_reminders,
    lead_webinar,
    open_markup,
    reminder_text,
    webinars_config,
)
from tests.test_academy_ad_leads import HEADER

MINSK = timezone(timedelta(hours=3))
AT24 = datetime(2026, 10, 24, 17, 30, tzinfo=MINSK)
SUPPORT = {
    "webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": "https://meet.google.com/abc-defg-hij"},
        "ig_bad": {"at": "завтра", "date": "x"},
        "ig_naive": {"at": "2026-10-24T17:30:00", "date": "x"},
    }
}


def test_config_skips_broken_entries_and_non_https_links():
    webinars = {w.tag: w for w in webinars_config(SUPPORT)}
    assert set(webinars) == {"ig_web24", "ig_web31"}
    assert webinars["ig_web24"].url == "" and webinars["ig_web31"].url.startswith("https://meet.google.com/")
    assert webinars_config({"webinars": {"ig_web24": {"at": "2026-10-24T17:30:00+03:00", "url": "http://x"}}})[0].url == ""
    assert webinars_config(None) == [] and webinars_config({}) == []


def test_texts_carry_date_time_and_link_without_promises():
    w = Webinar("ig_web24", AT24, "24 октября", "https://meet.google.com/abc")
    day = reminder_text("day", w)
    assert day.startswith("Завтра, 24 октября, в 17:30 по Минску — бесплатный вебинар Александры Пармон по композиции.")
    assert "Ссылку на эфир пришлём сюда, в этот чат." in day
    assert reminder_text("two_hours", w) == (
        "Сегодня в 17:30 по Минску — вебинар по композиции с Александрой Пармон. Ссылку пришлём за час до начала."
    )
    # Ссылки в тексте нет — кнопка «Присоединиться к вебинару» (нажатие пишется, 24.10.2026).
    assert reminder_text("hour", w) == (
        "Через час начинаем, в 17:30 по Минску.\n\nВойти в эфир — кнопкой «Присоединиться к вебинару» ниже."
    )
    assert reminder_text("five", w) == "Начинаем через 5 минут. Заходите — кнопка «Присоединиться к вебинару» ниже."
    assert "meet.google.com" not in reminder_text("hour", w) + reminder_text("five", w)
    assert _link_markup(w) == {"inline_keyboard": [[{"text": "Присоединиться к вебинару", "callback_data": "wjoin:ig_web24"}]]}
    assert open_markup(w)["inline_keyboard"][0][0]["url"] == "https://meet.google.com/abc"
    for kind in ("day", "two_hours", "hour", "five"):
        assert "подар" not in reminder_text(kind, w).lower()


def test_schedule_windows_and_link_gate():
    no_link = Webinar("ig_web24", AT24, "24 октября", "")
    with_link = Webinar("ig_web24", AT24, "24 октября", "https://meet.google.com/abc")
    at = lambda h, m, d=24: datetime(2026, 10, d, h, m, tzinfo=MINSK)  # noqa: E731
    assert [k for k, _ in due_reminders(no_link, at(17, 31, 23))] == ["day"]
    assert due_reminders(no_link, at(17, 29, 23)) == []
    assert [k for k, _ in due_reminders(no_link, at(15, 31))] == ["two_hours"]
    # Без ссылки «за час» и «за 5 минут» не уходят; со ссылкой — уходят.
    assert due_reminders(no_link, at(16, 31)) == [] and due_reminders(no_link, at(17, 26)) == []
    assert [k for k, _ in due_reminders(with_link, at(16, 31))] == ["hour"]
    assert [k for k, _ in due_reminders(with_link, at(17, 26))] == ["five"]
    # Ссылка пришла в 16:55 — «Через час начинаем» уже не шлём, уйдёт только «за 5 минут».
    assert due_reminders(with_link, at(16, 55)) == []
    # Окно закрыто: «за сутки» не шлём на следующий день.
    assert [k for k, _ in due_reminders(with_link, at(9, 0))] == []


CSV = "\n".join([
    HEADER,
    "l:601,2026-10-22T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,"
    "бесплатный_вебинар_24_октября,нет,,Дарья Звонкова,p:+375291112233,CREATED,,",
    # Имя формы одно на обе даты — решает выбранный пункт «31 октября».
    "l:602,2026-10-22T11:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,"
    "бесплатный_вебинар_31_октября,нет,,Светлана,p:+375447778899,CREATED,,",
])


def test_lead_webinar_by_interest_and_call_list():
    webinars = webinars_config(SUPPORT)
    dasha, sveta = parse_leads(CSV)
    assert (lead_webinar(dasha, webinars), lead_webinar(sveta, webinars), lead_webinar(None, webinars)) == (
        "ig_web24", "ig_web31", None,
    )
    [text] = call_list_texts(webinars[0], [dasha])
    assert text.startswith("📞 Не в боте — позвонить или написать до 17:30 (1 чел.) · вебинар 24 октября")
    assert "1. <b>Дарья Звонкова</b>\n📞 +375291112233\n💬 <a href=\"https://wa.me/375291112233\">WhatsApp</a>" in text
    # Длинный список делится на сообщения, каждое с заголовком.
    many = call_list_texts(webinars[0], [dasha] * 40)
    assert len(many) > 1 and all("Не в боте" in t for t in many) and all(len(t) < 4096 for t in many)


def test_call_list_carries_personal_bot_link_only_with_bot_url():
    """Личная ссылка ig_<вебинар>_<id заявки> (19.10.2026): открыв её, человек находит свою строку сам."""
    webinars = webinars_config(SUPPORT)
    dasha, sveta = parse_leads(CSV)
    [text] = call_list_texts(webinars[0], [dasha, sveta], bot_url="https://t.me/ALMIRA_I_bot")
    assert "🔗 Личная ссылка на бота: https://t.me/ALMIRA_I_bot?start=ig_web24_601" in text
    assert text.count("Личная ссылка") == 2 and "?start=ig_web24_602" in text
    [plain] = call_list_texts(webinars[0], [dasha])
    assert "Личная ссылка" not in plain and "?start=" not in plain
    # Длинный список с ссылками по-прежнему делится на сообщения короче лимита Telegram.
    many = call_list_texts(webinars[0], [dasha] * 40, bot_url="https://t.me/ALMIRA_I_bot")
    assert len(many) > 1 and all(len(t) < 4096 for t in many)


async def test_showcase_bot_url_is_the_showcase_bot_only():
    """Личная ссылка — только на бота витрины; не нашли его — пусто (не бот WWC процесса)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.academy.webinars import showcase_bot_url

    showcase = SimpleNamespace(telegram_binding_id="almira-academy-bot")
    with patch("app.academy.showcases.binding_username", AsyncMock(return_value="ALMIRA_I_bot")) as found:
        assert await showcase_bot_url(showcase) == "https://t.me/ALMIRA_I_bot"
    assert found.await_args.args == ("almira-academy-bot",)
    with patch("app.academy.showcases.binding_username", AsyncMock(return_value=None)):
        assert await showcase_bot_url(showcase) == ""
    with patch("app.academy.showcases.binding_username", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await showcase_bot_url(showcase) == ""


def test_confirm_texts_and_move_buttons_only_to_future_webinars():
    from app.academy.webinars import confirm_text, correction_text, move_markup, moved_text

    webinars = webinars_config(SUPPORT)
    w24, w31 = webinars
    assert confirm_text(w31) == "Вы записаны на 31 октября, начало в 17:30 по Минску."
    assert moved_text(w24) == "Готово: вы записаны на 24 октября, начало в 17:30 по Минску."
    assert correction_text(w31) == "Поправка: вы записаны на 31 октября, начало в 17:30 по Минску."
    before = datetime(2026, 10, 18, 12, 0, tzinfo=MINSK)
    assert move_markup(webinars, "ig_web31", before) == {
        "inline_keyboard": [[{"text": "Перенести на 24 октября", "callback_data": "wbn:ig_web24"}]],
    }
    # 24-е прошло — переносить на него нельзя, кнопок нет.
    assert move_markup(webinars, "ig_web31", datetime(2026, 10, 25, 12, 0, tzinfo=MINSK)) is None


def test_bot_choice_beats_form_choice_beats_tag():
    from app.academy.ad_leads import Opened
    from app.academy.webinars import person_webinar

    webinars = webinars_config(SUPPORT)
    dasha, sveta = parse_leads(CSV)  # Дарья — 24, Светлана — 31 (в форме)
    by_id = {lead.lead_id: lead for lead in (dasha, sveta)}
    assert person_webinar(Opened(1, None, "", "602", None, "ig_web24"), by_id, webinars) == "ig_web31"
    assert person_webinar(Opened(1, None, "", "602", None, "ig_web24", "ig_web24"), by_id, webinars) == "ig_web24"
    assert person_webinar(Opened(1, None, "", None, None, "ig_web24"), by_id, webinars) == "ig_web24"
    # Выбор в боте на неизвестный вебинар не учитывается.
    assert person_webinar(Opened(1, None, "", "602", None, "ig_web24", "ig_old"), by_id, webinars) == "ig_web31"


def test_paid_mark_in_any_admin_column_but_not_bot_flag():
    """Кирилл пишет «оплатил» в безымянную колонку после «Комментарий» — это тоже оплата; «В боте» — не пометка."""
    from app.academy.webinars import is_paid

    head = HEADER + ",,,В боте"
    csv_text = "\n".join([
        head,
        CSV.splitlines()[1] + ",написал,оплатил 10к,✅ да",
        CSV.splitlines()[2] + ",написал,,нет",
    ])
    paid, unpaid = parse_leads(csv_text)
    assert paid.status == "написал | оплатил 10к" and is_paid(paid)
    assert unpaid.status == "написал" and not is_paid(unpaid)
    # Колонки формы читаются как раньше, несмотря на безымянные колонки.
    assert (paid.name, paid.phone) == ("Дарья Звонкова", "+375291112233")


def test_followup_text_paid_mark_and_past_date_in_form():
    from dataclasses import replace

    from app.academy.webinars import followup_markup, followup_text, is_paid, next_webinar

    support = {"webinars": {
        **SUPPORT["webinars"],
        "ig_web24": {**SUPPORT["webinars"]["ig_web24"], "followup": {"at": "2026-10-25T12:00:00+03:00", "to": "ig_web31"}},
    }}
    webinars = webinars_config(support)
    w24, w31 = webinars
    assert w24.followup_at == datetime(2026, 10, 25, 12, 0, tzinfo=MINSK) and w24.followup_to == "ig_web31"
    assert followup_text(w24, w31) == (
        "Не получилось прийти на вебинар 24 октября или хотите пересмотреть? "
        "31 октября в 17:30 по Минску — повтор эфира. Записать вас?"
    )
    assert followup_markup(w31) == {"inline_keyboard": [[{"text": "Записаться на 31 октября", "callback_data": "wbn:ig_web31"}]]}
    dasha, _ = parse_leads(CSV)
    assert not is_paid(dasha) and is_paid(replace(dasha, status="Оплатил 10к")) and not is_paid(None)
    assert is_paid(replace(dasha, status="написал | Бронь 50"))  # бронь — предложения после эфира не идут
    # Форма всё ещё предлагает 24 октября: выбрал его после начала эфира — это 31-е.
    late = replace(dasha, created=datetime(2026, 10, 24, 18, 0, tzinfo=MINSK))
    assert lead_webinar(late, webinars) == "ig_web31"
    assert next_webinar(webinars, datetime(2026, 11, 1, tzinfo=MINSK)) is None


def test_warmup_config_texts_and_windows():
    """Прогрев (20.10.2026): тексты из настройки, {date}/{time}, окно 2 часа; битые пункты пропускаются."""
    from app.academy.webinars import due_messages, warmup_config

    support = {"webinar_warmup": [
        {"key": "w1", "before_min": 4650, "text": "Через три дня, {date} в {time}, — вебинар."},
        {"key": "w2", "before_min": 2790, "text": "Идея на сегодня."},
        {"key": "BAD KEY", "before_min": 100, "text": "x"},
        {"key": "w9", "before_min": "много", "text": "x"},
        {"key": "w8", "before_min": 120, "text": "  "},
    ]}
    warmups = warmup_config(support)
    assert [w.key for w in warmups] == ["w1", "w2"]
    webinar = webinars_config(SUPPORT)[0]  # 24 октября, 17:30, без ссылки
    at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=MINSK)  # noqa: E731
    [(kind, moment, text, markup, photos)] = due_messages(webinar, warmups, at(21, 12, 5))
    assert (kind, moment, markup, photos) == ("warm_w1", at(21, 12, 0), None, ())
    assert text == "Через три дня, 24 октября в 17:30, — вебинар."
    assert [m[0] for m in due_messages(webinar, warmups, at(22, 19, 1))] == ["warm_w2"]
    assert due_messages(webinar, warmups, at(21, 14, 1)) == []  # окно 2 часа закрылось
    assert warmup_config(None) == [] and warmup_config({"webinar_warmup": "не список"}) == []


def test_warmup_photos_and_button():
    from app.academy.webinars import due_messages, warmup_config

    photo = "AgACAgIAAxkDAAIBQ2c_" + "x" * 30
    support = {"webinar_warmup": [{
        "key": "p3", "before_min": 2790, "text": "Работы учеников.", "photos": [photo, photo + "y", "плохой id"],
        "button": {"text": "Программа курса", "url": "https://almira.by/composition-play-of-shapes/"},
    }, {"key": "p1", "before_min": 5670, "text": "Миф.", "button": {"text": "x", "url": "http://нет"}}]}
    p3, p1 = warmup_config(support)
    assert p3.photos == (photo, photo + "y") and p1.photos == () and p1.button is None
    webinar = webinars_config(SUPPORT)[0]
    [(_, _, _, markup, photos)] = due_messages(webinar, [p3], datetime(2026, 10, 22, 19, 1, tzinfo=MINSK))
    assert photos == (photo, photo + "y")
    assert markup == {"inline_keyboard": [[{"text": "Программа курса", "url": "https://almira.by/composition-play-of-shapes/"}]]}


async def test_due_notification_sends_photos_then_text():
    """Сначала альбом (или одно фото), потом текст с кнопкой; фото не ушло — текст всё равно уходит."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.jobs import worker

    binding = SimpleNamespace(bot_token="123:abc", tenant=SimpleNamespace(tenant_id="whieda"))
    calls = []
    album = AsyncMock(side_effect=lambda **kw: calls.append(("album", kw["photos"])) or {"ok": True})
    photo = AsyncMock(side_effect=lambda **kw: calls.append(("photo", kw["photo_url"])) or {"ok": True})
    text = AsyncMock(side_effect=lambda **kw: calls.append(("text", kw["text"])) or {"ok": True})
    with patch.object(worker, "send_telegram_media_group", album), patch.object(worker, "send_telegram_photo", photo),             patch.object(worker, "send_telegram_text", text):
        await worker._send_due_notification(binding, {"chat_id": "1", "text": "Пост", "photos": ["a" * 25, "b" * 25]})
        await worker._send_due_notification(binding, {"chat_id": "1", "text": "Пост 2", "photos": ["c" * 25]})
    assert calls == [("album", ["a" * 25, "b" * 25]), ("text", "Пост"), ("photo", "c" * 25), ("text", "Пост 2")]
    calls.clear()
    broken = AsyncMock(side_effect=RuntimeError("telegram down"))
    with patch.object(worker, "send_telegram_photo", broken), patch.object(worker, "send_telegram_text", text):
        await worker._send_due_notification(binding, {"chat_id": "1", "text": "Пост 3", "photos": ["d" * 25]})
    assert calls == [("text", "Пост 3")]


def test_ticket_card_says_who_it_is_in_the_sheet():
    """Шапка обращения администратору (22.10.2026): «Svetik» — это Светлана Самохвал из таблицы, контакты, вебинар."""
    from app.academy.ad_leads import Opened, parse_leads
    from app.academy.webinars import card_text, webinars_config

    webinars = webinars_config({"webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
    }})
    leads = parse_leads("\n".join([
        HEADER,
        "l:701,2026-10-17T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,бесплатный_вебинар_24_октября,нет,,"
        "Светлана Самохвал,p:+375291000001,CREATED,написал,перенесла на 31",
    ]))
    svetik = Opened(1068389443, None, "Svetik", "701", datetime(2026, 10, 17, 7, 5, tzinfo=timezone.utc), "ig_web24", "ig_web31")
    text = card_text(svetik, leads, webinars, username=None, name="Svetik")
    assert text.splitlines() == [
        "📋 В таблице: <b>Светлана Самохвал</b> · вебинар 31 октября",
        "📞 +375291000001",
        '💬 <a href="https://wa.me/375291000001">WhatsApp</a> · <a href="https://t.me/+375291000001">Telegram по номеру</a>',
        "📝 написал | перенесла на 31",
    ]
    # Бота открыл не по ссылке и в таблице не нашёлся — так и пишем, вебинар по метке.
    stranger = Opened(5, "nobody", "Кто-то", None, datetime(2026, 10, 22, 7, 5, tzinfo=timezone.utc), "ig_web24")
    assert card_text(stranger, leads, webinars, username="nobody", name="Кто-то") == "📋 В таблице заявок не найдено · вебинар 24 октября"
    # Записи «открыл бота» нет, но имя и фамилия совпали — строка находится, вебинар по форме.
    assert card_text(None, leads, webinars, username=None, name="Самохвал Светлана").startswith(
        "📋 В таблице: <b>Светлана Самохвал</b> · вебинар 24 октября"
    )


def test_admin_column_efir_decides_the_webinar():
    """Колонка «Эфир» (Кирилл после звонка пишет 24 или 31) важнее пункта формы и кнопки в боте (23.10.2026)."""
    from app.academy.ad_leads import Opened
    from app.academy.webinars import person_webinar

    webinars = webinars_config({"webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
    }})
    head = HEADER + ",В боте,Эфир"
    rows = [
        "l:801,2026-10-20T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,бесплатный_вебинар_24_октября,нет,,"
        "Ирина Пример,p:+375291000801,CREATED,написал,,✅ да,на 31",
        "l:802,2026-10-20T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,бесплатный_вебинар_31_октября,нет,,"
        "Олег Пример,p:+375291000802,CREATED,,,нет,24.10",
        "l:803,2026-10-20T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,бесплатный_вебинар_24_октября,нет,,"
        "Без Пометки,p:+375291000803,CREATED,,,нет,",
    ]
    irina, oleg, plain = parse_leads("\n".join([head, *rows]))
    assert (irina.webinar_mark, oleg.webinar_mark, plain.webinar_mark) == ("на 31", "24.10", "")
    assert [lead_webinar(x, webinars) for x in (irina, oleg, plain)] == ["ig_web31", "ig_web24", "ig_web24"]
    by_id = {x.lead_id: x for x in (irina, oleg, plain)}
    # Нажал в боте «Перенести на 24», а Кирилл после звонка поставил 31 — верим таблице.
    pressed = Opened(1, None, "Ирина", "801", None, "ig_web24", "ig_web24")
    assert person_webinar(pressed, by_id, webinars) == "ig_web31"
    # Без пометки — как раньше: кнопка в боте.
    assert person_webinar(Opened(3, None, "Без", "803", None, "ig_web24", "ig_web31"), by_id, webinars) == "ig_web31"


# Серия «анатомия» (23.10.2026): свои даты, форма, тема напоминаний и перенос только внутри серии.
ANATOMY = {
    "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
    "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
    "ig_an28": {"at": "2026-10-28T19:30:00+03:00", "date": "28 октября", "url": "", "series": "anatomy",
                "form": "анатом", "subject": "по пластической анатомии", "label": "анатомия", "greeting": "Привет, анатомия"},
    "ig_an07": {"at": "2026-11-07T18:00:00+03:00", "date": "7 ноября", "url": "", "series": "anatomy",
                "form": "анатом", "subject": "по пластической анатомии", "label": "анатомия", "greeting": "Привет, анатомия"},
}
AN_HEADER = (
    "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,form_name,is_organic,"
    "platform,какой_эфир_удобнее?,есть_ли_у_вас_база_академического_рисунка?,ваш_ник_в_telegram_(для_ссылки_на_эфир),"
    "полное_имя,номер_телефона,lead_status"
)


def test_anatomy_series_dates_texts_and_moves_stay_inside_the_series():
    from app.academy.ad_leads import Opened
    from app.academy.webinars import Warmup, due_messages, move_markup, person_webinar

    webinars = webinars_config({"webinars": ANATOMY})
    an28 = next(w for w in webinars if w.tag == "ig_an28")
    assert (an28.series, an28.form, an28.label) == ("anatomy", "анатом", "анатомия")
    assert "бесплатный вебинар Александры Пармон по пластической анатомии" in reminder_text("day", an28)
    assert reminder_text("two_hours", an28).startswith("Сегодня в 19:30 по Минску — вебинар по пластической анатомии")
    leads = parse_leads("\n".join([
        AN_HEADER,
        "l:901,2026-10-23T10:00:00+03:00,,,,,,,f:9,Вебинар анатомия 28.10 и 7.11,false,ig,"
        "\"вебинар_7_ноября,_суббота,_18:00\",немного,,Анна Анатомова,p:+375291000901,CREATED",
        "l:902,2026-10-28T20:00:00+03:00,,,,,,,f:9,Вебинар анатомия 28.10 и 7.11,false,ig,"
        "\"вебинар_28_октября,_среда,_19:30\",нет,,Поздний Человек,p:+375291000902,CREATED",
        "l:903,2026-10-23T10:00:00+03:00,,,,,,,f:9,Вебинар анатомия 28.10 и 7.11,false,ig,,да,,Без Выбора,p:+375291000903,CREATED",
    ]))
    chose07, late, nochoice = leads
    assert chose07.base == "Немного" and chose07.interest.startswith("Вебинар 7 ноября")
    assert lead_webinar(chose07, webinars) == "ig_an07"
    assert lead_webinar(late, webinars) == "ig_an07"  # выбрал 28.10 уже после эфира — ближайший, 7.11
    # Без выбора — дата из имени формы: первая дата серии («28.10» в «… 28.10 и 7.11»), не композиция.
    assert lead_webinar(nochoice, webinars) == "ig_an28"
    # Перенос — только внутри серии: под анатомией нет кнопок 24 и 31 октября.
    rows = move_markup(webinars, "ig_an28", datetime(2026, 10, 23, 12, tzinfo=MINSK))["inline_keyboard"]
    assert [r[0]["callback_data"] for r in rows] == ["wbn:ig_an07"]
    # Запись анатомии со строкой композиции — строка не её, вебинар по метке.
    by_id = {"701": parse_leads(HEADER + "\nl:701,2026-10-20T10:00:00+03:00,,,,,,,f:4,Вебинар композиция 24.10,false,ig,"
                                 "бесплатный_вебинар_24_октября,нет,,Анна Ли,p:+375291000001,CREATED,,")[0]}
    stray = Opened(1, None, "Анна", "701", None, "ig_an28", None, "anatomy")
    assert person_webinar(stray, by_id, webinars) == "ig_an28"
    # Прогрев композиции к анатомии не уходит.
    warm = [Warmup("p1", timedelta(minutes=60), "Прогрев {date}")]
    assert due_messages(an28, warm, an28.at - timedelta(minutes=59)) == []


def test_post_webinar_offer_goes_after_the_start_with_action_buttons():
    """О1 (24.10.2026): after_min — через 2 часа после начала, окно 2 часа; кнопки «Записаться на курс»,
    «Написать администратору», «Записаться на повтор 31.10»."""
    from app.academy.webinars import copy_header, due_messages, warmup_config

    webinars = webinars_config({"webinars": ANATOMY})
    w24 = next(w for w in webinars if w.tag == "ig_web24")
    items = warmup_config({"webinar_warmup": [{
        "key": "o1", "after_min": 120, "text": "Эфир {date} завершился.",
        "buttons": [[{"text": "Записаться на курс", "action": "signup"}],
                    [{"text": "Написать администратору", "action": "admin"}],
                    [{"text": "Записаться на повтор 31.10", "action": "move:ig_web31"}],
                    [{"text": "плохая", "action": "rm -rf"}]],
    }]})
    assert items[0].after is True
    assert due_messages(w24, items, w24.at + timedelta(minutes=119)) == []
    (kind, moment, text, markup, photos), = due_messages(w24, items, w24.at + timedelta(minutes=121))
    assert (kind, moment, text) == ("post_o1", w24.at + timedelta(minutes=120), "Эфир 24 октября завершился.")
    assert markup["inline_keyboard"] == [
        [{"text": "Записаться на курс", "callback_data": "wsign:ig_web24"}],
        [{"text": "Написать администратору", "callback_data": "shs:open:admin"}],
        [{"text": "Записаться на повтор 31.10", "callback_data": "wbn:ig_web31"}],
    ]
    assert due_messages(w24, items, w24.at + timedelta(minutes=241)) == []
    assert "после эфира O1" in copy_header(w24, "post_o1", 5)


def test_message_can_be_limited_to_some_webinars():
    from app.academy.webinars import due_messages, warmup_config

    webinars = webinars_config({"webinars": ANATOMY})
    w24, w31 = (next(w for w in webinars if w.tag == t) for t in ("ig_web24", "ig_web31"))
    items = warmup_config({"webinar_warmup": [{"key": "o1", "after_min": 120, "text": "О1", "tags": ["ig_web24"]}]})
    assert [m[0] for m in due_messages(w24, items, w24.at + timedelta(minutes=121))] == ["post_o1"]
    assert due_messages(w31, items, w31.at + timedelta(minutes=121)) == []


def test_moved_webinar_keeps_old_form_date():
    """Анатомию 28.10 перенесли на 4.11 (26.10.2026): форма ещё предлагает «28 октября» — это 4 ноября."""
    from app.academy.ad_leads import Lead

    support = {"webinars": {
        "ig_an28": {"at": "2026-11-04T19:30:00+03:00", "date": "4 ноября", "series": "anatomy", "form": "анатом",
                    "was": ["28 октября", "28.10"]},
        "ig_an07": {"at": "2026-11-07T18:00:00+03:00", "date": "7 ноября", "series": "anatomy", "form": "анатом"},
    }}
    webinars = webinars_config(support)
    lead = Lead("901", datetime(2026, 10, 26, 10, 0, tzinfo=MINSK), "Ирина", "+375291112233", None, "", None,
                "бесплатный_вебинар_28_октября", "ig", "Вебинар анатомия 28.10")
    assert lead_webinar(lead, webinars) == "ig_an28"
    assert webinars_config(support)[0].was == ("28 октября", "28.10")
