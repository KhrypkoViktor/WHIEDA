"""Напоминания о вебинарах витрины (18.10.2026): настройка, тексты, расписание, список «не в боте»."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.academy.ad_leads import parse_leads
from app.academy.webinars import (
    Webinar,
    call_list_texts,
    due_reminders,
    lead_webinar,
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
    assert "https://meet.google.com/abc" in reminder_text("hour", w) and "Начало в 17:30 по Минску." in reminder_text("hour", w)
    assert reminder_text("five", w) == "Начинаем через 5 минут. Заходите: https://meet.google.com/abc"
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
    # Форма всё ещё предлагает 24 октября: выбрал его после начала эфира — это 31-е.
    late = replace(dasha, created=datetime(2026, 10, 24, 18, 0, tzinfo=MINSK))
    assert lead_webinar(late, webinars) == "ig_web31"
    assert next_webinar(webinars, datetime(2026, 11, 1, tzinfo=MINSK)) is None
