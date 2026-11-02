"""Дожим после вебинаров 3–8.11.2026 (Виктор, 02.11.2026): кружки, сегменты came/missed, «Что остановило?»,
доступ к записи на N часов. Настройка — ``academy_showcases.support``, тексты правятся без выпуска."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.academy.webinars import (
    Due,
    Grant,
    Warmup,
    Webinar,
    due_items,
    due_messages,
    warmup_config,
    webinars_config,
    why_config,
    why_markup,
)
from app.telegram.processor import process_core_telegram_update
from tests.test_academy_showcase_bot import ALMIRA, almira_binding  # noqa: F401 — фикстура

MINSK = timezone(timedelta(hours=3))
CIRCLE_A = "DQACAgIAAxkBAAIB" + "a" * 40  # file_id кружка
CIRCLE_B = "DQACAgIAAxkBAAIB" + "b" * 40
WEBINARS = {
    "ig_web24": {"at": "2026-11-03T19:30:00+03:00", "date": "3 ноября", "url": "https://meet.google.com/abc-defg-hij",
                 "video_notes": {"day": CIRCLE_A, "hour": CIRCLE_B, "five": "короткий", "week": CIRCLE_A, "two_hours": 5}},
    "ig_an07": {"at": "2026-11-07T18:00:00+03:00", "date": "7 ноября", "url": "", "series": "anatomy", "form": "анатом",
                "label": "анатомия"},
}
BASE = "https://academy.almira.by"


def at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=MINSK)


# ---- настройка: кружок, сегмент, grant ---------------------------------------------------------------


def test_video_note_is_checked_like_a_photo_but_may_be_up_to_255():
    ok255 = "A" * 255
    items = warmup_config({"webinar_warmup": [
        {"key": "a", "before_min": 100, "text": "т", "video_note": CIRCLE_A},
        {"key": "b", "before_min": 100, "text": "т", "video_note": ok255},
        {"key": "c", "before_min": 100, "text": "т", "video_note": "A" * 256},
        {"key": "d", "before_min": 100, "text": "т", "video_note": "коротко"},
        {"key": "e", "before_min": 100, "text": "т", "video_note": "A" * 19},
        {"key": "f", "before_min": 100, "text": "т", "video_note": "бот " + "A" * 30},
        {"key": "g", "before_min": 100, "text": "т"},
        {"key": "h", "before_min": 100, "text": "т", "video_note": ["не", "строка"]},
    ]})
    # Битый file_id — не повод терять сообщение: кружка нет, текст идёт как раньше.
    assert [(w.key, w.video_note) for w in items] == [
        ("a", CIRCLE_A), ("b", ok255), ("c", ""), ("d", ""), ("e", ""), ("f", ""), ("g", ""), ("h", ""),
    ]


def test_reminder_video_notes_config_keeps_only_standard_keys_and_good_ids():
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    assert dict(web.video_notes) == {"day": CIRCLE_A, "hour": CIRCLE_B}
    assert webinars_config({"webinars": {"x": {"at": "2026-11-03T19:30:00+03:00", "video_notes": "не словарь"}}})[0].video_notes == ()


def test_reminder_goes_with_its_own_circle_and_the_join_button_stays_under_the_text():
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    day = {d.key: d for d in due_items(web, [], web.at - timedelta(hours=24, minutes=-1))}
    assert day["day"].video_note == CIRCLE_A and day["day"].markup is None
    hour = {d.key: d for d in due_items(web, [], web.at - timedelta(minutes=59))}["hour"]
    assert hour.video_note == CIRCLE_B
    assert hour.markup == {"inline_keyboard": [[{"text": "Присоединиться к вебинару", "callback_data": "wjoin:ig_web24"}]]}
    # За 5 минут кружка в настройке нет — как раньше.
    assert {d.key: d for d in due_items(web, [], web.at - timedelta(minutes=4))}["five"].video_note == ""
    # Прежний вид для проверок — по-прежнему пятёрки без кружка.
    assert due_messages(web, [], web.at - timedelta(minutes=59))[0][0] == "hour"
    assert len(due_messages(web, [], web.at - timedelta(minutes=59))[0]) == 5


def test_audience_is_only_came_or_missed_and_a_typo_drops_the_item_instead_of_widening_it():
    items = warmup_config({"webinar_warmup": [
        {"key": "c1", "after_min": 180, "text": "были", "audience": "came"},
        {"key": "m1", "after_min": 180, "text": "не были", "audience": " Missed "},
        {"key": "all", "after_min": 180, "text": "все"},
        {"key": "typo", "after_min": 180, "text": "опечатка", "audience": "cam"},
        # До эфира сегмент игнорируется — даже с опечаткой элемент жив и идёт всем.
        {"key": "pre", "before_min": 120, "text": "до", "audience": "came"},
        {"key": "pre2", "before_min": 120, "text": "до", "audience": "ерунда"},
    ]})
    assert [(w.key, w.audience) for w in items] == [("c1", "came"), ("m1", "missed"), ("all", ""), ("pre", ""), ("pre2", "")]
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    due = {d.key: d for d in due_items(web, items, web.at + timedelta(minutes=181))}
    assert {k: v.audience for k, v in due.items()} == {"post_c1": "came", "post_m1": "missed", "post_all": ""}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"course": "zapis-efira", "hours": 48}, Grant("zapis-efira", 48)),
        ({"course": "zapis-efira", "hours": "24"}, Grant("zapis-efira", 24)),
        ({"course": "zapis-efira", "hours": 1440}, Grant("zapis-efira", 1440)),
        ({"course": "zapis-efira", "hours": 0}, None),
        ({"course": "zapis-efira", "hours": 1441}, None),
        ({"course": "zapis-efira", "hours": True}, None),
        ({"course": "zapis-efira", "hours": "много"}, None),
        ({"course": "zapis-efira"}, None),
        ({"course": "../evil", "hours": 48}, None),
        ({"course": "Zapis", "hours": 48}, None),
        ({"hours": 48}, None),
        ("48", None),
    ],
)
def test_grant_config(raw, expected):
    [item] = warmup_config({"webinar_warmup": [{"key": "g", "after_min": 60, "text": "т", "grant": raw}]})
    assert item.grant == expected


def test_course_button_needs_a_grant_and_becomes_a_link_to_that_course():
    buttons = [[{"text": "Смотреть запись", "action": "course"}], [{"text": "Написать", "action": "admin"}]]
    without, with_grant = warmup_config({"webinar_warmup": [
        {"key": "a", "after_min": 60, "text": "т", "buttons": buttons},
        {"key": "b", "after_min": 60, "text": "т", "buttons": buttons, "grant": {"course": "zapis-efira", "hours": 48}},
    ]})
    # Без grant курс неизвестен — кнопка «Смотреть запись» пропадает, остальное остаётся.
    assert without.buttons == ((("Написать", "action", "admin"),),)
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    [due] = due_items(web, [with_grant], web.at + timedelta(minutes=61), base_url=BASE)
    assert due.grant == Grant("zapis-efira", 48) and due.login is True
    # Кнопка стоит там, где её поставили в настройке (первым рядом), а не последней строкой.
    assert due.markup["inline_keyboard"] == [
        [{"text": "Смотреть запись", "url": f"{BASE}/academy/?course=zapis-efira"}],
        [{"text": "Написать", "callback_data": "shs:open:admin"}],
    ]
    # Адреса витрины нет — ссылки нет, и входа на сайт не нужно.
    [bare] = due_items(web, [with_grant], web.at + timedelta(minutes=61))
    assert bare.login is False and bare.markup["inline_keyboard"] == [[{"text": "Написать", "callback_data": "shs:open:admin"}]]
    [plain] = due_items(web, [without], web.at + timedelta(minutes=61), base_url=BASE)
    assert plain.login is False and plain.grant is None


def test_post_element_carries_circle_photo_and_the_before_element_ignores_audience():
    photo = "AgACAgIAAxkDAAIBQ2c_" + "x" * 30
    items = warmup_config({"webinar_warmup": [
        {"key": "c1", "after_min": 180, "text": "были", "audience": "came", "video_note": CIRCLE_A, "photos": [photo]},
        {"key": "p1", "before_min": 120, "text": "до", "audience": "missed", "video_note": CIRCLE_B},
    ]})
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    [post] = due_items(web, items, web.at + timedelta(minutes=181))
    assert (post.key, post.video_note, post.photos, post.audience) == ("post_c1", CIRCLE_A, (photo,), "came")
    [pre] = [d for d in due_items(web, items, web.at - timedelta(minutes=119)) if d.key.startswith("warm_")]
    assert (pre.key, pre.video_note, pre.audience) == ("warm_p1", CIRCLE_B, "")


# ---- «Что остановило?» --------------------------------------------------------------------------------

WHY = {
    "reasons": [
        {"code": "price", "text": "Дорого"},
        {"code": "time", "text": "Нет времени"},
        {"code": "BAD CODE", "text": "плохой код"},
        {"code": "price", "text": "Повтор кода"},
        {"code": "toolongcodeforus1", "text": "17 знаков"},
        {"code": "empty", "text": "  "},
    ],
    "answers": {
        "price": {
            "text": "Понимаю. Давайте разберём.", "label": "лист <b>ожидания</b>", "open": "admin",
            "buttons": [[{"text": "Записаться", "action": "signup"}, {"text": "Сайт", "url": "https://almira.by/x"}],
                        [{"text": "Что?", "action": "why"}, {"text": "Запись", "action": "course"}],
                        [{"text": "Повтор", "action": "move:ig_web31"}, {"text": "дыра", "action": "rm -rf"}]],
            "series": {
                "anatomy": {"text": "Для анатомии иначе.", "buttons": []},
                "pro": {"text": "Для про.", "label": "про", "open": ""},
                "broken": "не словарь",
            },
        },
        "time": {"text": "  "},
        "BAD": {"text": "плохой код ответа"},
        "oops": "не словарь",
    },
}


def test_why_buttons_expand_into_one_reason_per_row():
    [item] = warmup_config({"webinar_warmup": [{
        "key": "o1", "after_min": 120, "text": "Что остановило?",
        "buttons": [[{"text": "Записаться", "action": "signup"}], [{"text": "Что остановило?", "action": "why"}],
                    [{"text": "Написать", "action": "admin"}]],
    }]})
    why = why_config({"why": WHY})
    assert why.reasons == (("price", "Дорого"), ("time", "Нет времени"))  # плохой и повторный код, пустой текст — мимо
    web = next(w for w in webinars_config({"webinars": WEBINARS}) if w.tag == "ig_web24")
    [due] = due_items(web, [item], web.at + timedelta(minutes=121), reasons=why.reasons)
    assert due.markup["inline_keyboard"] == [
        [{"text": "Записаться", "callback_data": "wsign:ig_web24"}],
        [{"text": "Дорого", "callback_data": "wwhy:ig_web24:price"}],
        [{"text": "Нет времени", "callback_data": "wwhy:ig_web24:time"}],
        [{"text": "Написать", "callback_data": "shs:open:admin"}],
    ]
    assert all(len(b["callback_data"].encode()) <= 64 for row in due.markup["inline_keyboard"] for b in row)
    # Нет настройки причин — кнопка «Что остановило?» ничего не рисует, остальное на месте.
    [bare] = due_items(web, [item], web.at + timedelta(minutes=121))
    assert [row[0]["callback_data"] for row in bare.markup["inline_keyboard"]] == ["wsign:ig_web24", "shs:open:admin"]


def test_why_button_in_the_middle_of_a_row_splits_it_and_long_callbacks_are_skipped():
    from app.academy.webinars import _rows_markup

    rows = ((("А", "action", "admin"), ("Что", "action", "why"), ("Б", "action", "signup")),)
    reasons = (("price", "Дорого"), ("c" * 16, "Длинный"))
    out = _rows_markup(rows, "ig_web24", reasons=reasons)
    assert [[b["text"] for b in row] for row in out] == [["А"], ["Дорого"], ["Длинный"], ["Б"]]
    # Метка вебинара длинная: wwhy:<метка>:<код> не влезает в 64 байта — причины пропущены, а не обрезаны.
    out = _rows_markup(rows, "ig_" + "x" * 60, reasons=reasons)
    assert [[b["text"] for b in row] for row in out] == [["А"], ["Б"]]


def test_why_answers_series_override_inherits_what_is_not_given():
    why = why_config({"why": WHY})
    assert set(why.answers) == {"price"}  # пустой текст, плохой код, не словарь — мимо
    base = why.answers["price"]
    assert (base.text, base.label, base.open) == ("Понимаю. Давайте разберём.", "лист ожидания", "admin")  # <b> убран
    # Кнопки ответа: url, signup, move; «Что остановило?» и «Смотреть запись» в ответе бессмысленны.
    assert [[b[2] for b in row] for row in base.buttons] == [["signup", "https://almira.by/x"], ["move:ig_web31"]]
    anatomy = base.for_series("anatomy")
    assert (anatomy.text, anatomy.buttons, anatomy.label, anatomy.open) == ("Для анатомии иначе.", (), "лист ожидания", "admin")
    pro = base.for_series("pro")
    assert (pro.text, pro.label, pro.open, pro.buttons) == ("Для про.", "про", "", base.buttons)
    assert base.for_series("") is base and base.for_series("нет такой") is base
    assert why_markup(base, "ig_web24")["inline_keyboard"] == [
        [{"text": "Записаться", "callback_data": "wsign:ig_web24"}, {"text": "Сайт", "url": "https://almira.by/x"}],
        [{"text": "Повтор", "callback_data": "wbn:ig_web31"}],
    ]
    assert why_markup(anatomy, "ig_an07") is None
    assert why_config(None).reasons == () and why_config({"why": "нет"}).answers == {}


# ---- доставка: кружок → фото → текст ------------------------------------------------------------------


def _worker_calls():
    from app.jobs import worker

    calls = []
    binding = SimpleNamespace(bot_token="123:abc", tenant=SimpleNamespace(tenant_id="whieda"))
    return worker, binding, calls


@pytest.mark.asyncio
async def test_circle_goes_first_then_photo_then_text_with_buttons():
    worker, binding, calls = _worker_calls()
    media = AsyncMock(side_effect=lambda chat, kind, file_id, **kw: calls.append(("media", kind, file_id, kw)) or {"ok": True})
    photo = AsyncMock(side_effect=lambda **kw: calls.append(("photo", kw["photo_url"])) or {"ok": True})
    text = AsyncMock(side_effect=lambda **kw: calls.append(("text", kw["text"], kw["reply_markup"])) or {"ok": True})
    markup = {"inline_keyboard": [[{"text": "Записаться", "callback_data": "wsign:ig_web24"}]]}
    payload = {"chat_id": "1", "text": "Пост", "photos": ["c" * 25], "video_note": CIRCLE_A, "reply_markup": markup}
    with patch.object(worker, "send_telegram_media", media), patch.object(worker, "send_telegram_photo", photo), patch.object(
        worker, "send_telegram_text", text
    ):
        await worker._send_due_notification(binding, payload)
    # У кружка нет ни подписи, ни кнопок: они только у текстового сообщения.
    assert calls == [("media", "video_note", CIRCLE_A, {"bot_token": "123:abc"}), ("photo", "c" * 25), ("text", "Пост", markup)]


@pytest.mark.asyncio
async def test_failed_or_ambiguous_circle_never_costs_the_text():
    from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown

    worker, binding, calls = _worker_calls()
    text = AsyncMock(side_effect=lambda **kw: calls.append(("text", kw["text"])) or {"ok": True})
    payload = {"chat_id": "1", "text": "Пост", "video_note": CIRCLE_A}
    for error in (TelegramDeliveryError("telegram_media_failed:400"), RuntimeError("down")):
        calls.clear()
        with patch.object(worker, "send_telegram_media", AsyncMock(side_effect=error)), patch.object(
            worker, "send_telegram_text", text
        ):
            await worker._send_due_notification(binding, payload)
        assert calls == [("text", "Пост")]
    # Неоднозначный итог кружка (таймаут): кружок не повторяем, а текст с кнопками всё равно уходит — главное в нём.
    calls.clear()
    with patch.object(worker, "send_telegram_media", AsyncMock(side_effect=TelegramDeliveryUnknown("telegram_send_ambiguous"))), patch.object(
        worker, "send_telegram_text", text
    ):
        await worker._send_due_notification(binding, payload)
    assert calls == [("text", "Пост")]


@pytest.mark.asyncio
async def test_no_video_note_means_no_media_call_and_the_course_link_is_signed_in_at_sending():
    worker, binding, calls = _worker_calls()
    media = AsyncMock(return_value={"ok": True})
    text = AsyncMock(side_effect=lambda **kw: calls.append(kw["reply_markup"]) or {"ok": True})
    login = AsyncMock(side_effect=lambda url, **kw: f"{url}#wwc-login=tok-{kw['telegram_user_id']}")
    markup = {"inline_keyboard": [
        [{"text": "Смотреть запись", "url": f"{BASE}/academy/?course=zapis-efira"}],
        [{"text": "Написать", "callback_data": "shs:open:admin"}],
    ]}
    with patch.object(worker, "send_telegram_media", media), patch.object(worker, "send_telegram_text", text), patch(
        "app.telegram.site_login.with_site_login", login
    ):
        await worker._send_due_notification(
            binding, {"chat_id": "301", "text": "Запись", "reply_markup": markup, "site_login_user_id": 301}
        )
    media.assert_not_called()
    assert calls[0]["inline_keyboard"][0] == [
        {"text": "Смотреть запись", "url": f"{BASE}/academy/?course=zapis-efira#wwc-login=tok-301"}
    ]
    assert calls[0]["inline_keyboard"][1] == [{"text": "Написать", "callback_data": "shs:open:admin"}]


# ---- кнопка «Что остановило?»: обработчик --------------------------------------------------------------

SHOWCASE = replace(ALMIRA, support={
    "admin_telegram_user_id": 5035800725,
    "webinars": {
        "ig_web24": {"at": "2026-11-03T19:30:00+03:00", "date": "3 ноября", "url": ""},
        "ig_an07": {"at": "2026-11-07T18:00:00+03:00", "date": "7 ноября", "url": "", "series": "anatomy", "form": "анатом",
                    "label": "анатомия"},
    },
    "why": {
        "reasons": [{"code": "price", "text": "Дорого"}, {"code": "think", "text": "Хочу подумать"}, {"code": "mute", "text": "Без ответа"}],
        "answers": {
            "price": {
                "text": "Понимаю про цену. Напишите сюда — подберём вариант.", "label": "лист ожидания", "open": "admin",
                "buttons": [[{"text": "Записаться", "action": "signup"}]],
                "series": {"anatomy": {"text": "Анатомия: цена другая.", "buttons": [[{"text": "Программа", "url": "https://almira.by/an/"}]]}},
            },
            "think": {"text": "Конечно, подумайте.", "buttons": [[{"text": "Админ", "action": "admin"}]]},
        },
    },
})


def _tap(data: str, *, user: int = 301, chat_type: str = "private") -> dict:
    return {"callback_query": {
        "id": "cb1", "data": data,
        "from": {"id": user, "first_name": "Анна", "last_name": "<b>П</b>", "username": "anna_p"},
        "message": {"message_id": 9, "chat": {"id": user, "type": chat_type}},
    }}


async def _press(whieda_tenant, almira_binding, data, *, enqueue=None, showcase=SHOWCASE, admin=5035800725, **kw):
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    answered = AsyncMock(return_value={"ok": True})
    enqueue = enqueue or AsyncMock(return_value={"created": True})
    opened = AsyncMock(return_value={"ok": True, "route": "showcase_support", "status": "ticket_opened"})

    class _Conn:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=showcase)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.answer_callback_query", answered), patch(
        "app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"author": 739736805, "admin": admin})
    ), patch("app.academy.webinars.lead_card", AsyncMock(return_value="📋 В таблице: <b>Анна Петрова</b>\n📞 +375291112233")), patch(
        "app.db.tenant_connection", lambda tenant_id: _Conn()
    ), patch("app.jobs.outbox.enqueue_outbox_event", enqueue), patch("app.telegram.showcase_bot.open_ticket", opened):
        result = await process_core_telegram_update(whieda_tenant, _tap(data, **kw), "t", binding=almira_binding)
    return result, sent, answered, enqueue, opened


@pytest.mark.asyncio
async def test_reason_button_answers_the_person_and_alerts_the_admin_with_the_card(whieda_tenant, almira_binding):
    result, sent, answered, enqueue, opened = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:think")
    assert (result["route"], result["known"], result["admin_alert"]) == ("showcase_why", True, True)
    answered.assert_awaited_once()
    assert sent.await_args.kwargs["text"] == "Конечно, подумайте."
    assert sent.await_args.kwargs["reply_markup"] == {"inline_keyboard": [[{"text": "Админ", "callback_data": "shs:open:admin"}]]}
    opened.assert_not_called()  # у этого ответа нет "open"
    kwargs = enqueue.await_args.kwargs
    assert kwargs["idempotency_key"] == "wwhy:almira:ig_web24:think:301"
    assert kwargs["payload"]["chat_id"] == "5035800725" and kwargs["payload"]["binding_id"] == "almira-academy-bot"
    assert kwargs["payload"]["text"] == (
        '❓ <b>Что остановило</b>: «Хочу подумать» — <b><a href="tg://user?id=301">Анна bП/b</a></b> (@anna_p)'
        " · композиция · после вебинара 3 ноября\n\n📋 В таблице: <b>Анна Петрова</b>\n📞 +375291112233"
    )


@pytest.mark.asyncio
async def test_answer_for_the_anatomy_series_replaces_text_and_buttons_and_the_alert_names_the_series(whieda_tenant, almira_binding):
    result, sent, _, enqueue, opened = await _press(whieda_tenant, almira_binding, "wwhy:ig_an07:price")
    assert result["admin_alert"] is True
    # Серия анатомии: свой текст и кнопки; «open» и «label» из основного ответа сохраняются.
    opened.assert_awaited_once()
    assert opened.await_args.kwargs["notice"] == "Анатомия: цена другая."
    assert opened.await_args.kwargs["notice_markup"] == {"inline_keyboard": [[{"text": "Программа", "url": "https://almira.by/an/"}]]}
    assert enqueue.await_args.kwargs["payload"]["text"].split("\n\n")[0].endswith(
        "(@anna_p) · анатомия · после вебинара 7 ноября · 📝 лист ожидания"
    )


@pytest.mark.asyncio
async def test_open_admin_makes_the_answer_the_opening_hint_instead_of_a_second_message(whieda_tenant, almira_binding):
    result, sent, _, enqueue, opened = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:price")
    assert result["admin_alert"] is True
    opened.assert_awaited_once()
    args = opened.await_args
    assert args.args[3] == "admin"  # kind
    assert args.kwargs["notice"] == "Понимаю про цену. Напишите сюда — подберём вариант."
    assert args.kwargs["notice_markup"] == {"inline_keyboard": [[{"text": "Записаться", "callback_data": "wsign:ig_web24"}]]}
    sent.assert_not_called()  # ответ ушёл самим открытием обращения: человек получает одно сообщение
    assert enqueue.await_args.kwargs["payload"]["text"].split("\n\n")[0].endswith("после вебинара 3 ноября · 📝 лист ожидания")


@pytest.mark.asyncio
async def test_open_admin_falls_back_to_a_plain_answer_without_an_admin_or_when_opening_fails(whieda_tenant, almira_binding):
    result, sent, _, enqueue, opened = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:price", admin=None)
    opened.assert_not_called()
    assert sent.await_args.kwargs["text"].startswith("Понимаю про цену")
    enqueue.assert_not_called()  # администратора нет — алерта нет, ответ человеку всё равно уходит
    assert result["admin_alert"] is False
    with patch("app.telegram.showcase_bot.open_ticket", AsyncMock(side_effect=RuntimeError("db"))):
        sent2 = AsyncMock(return_value={"ok": True})
        with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=SHOWCASE)), patch(
            "app.telegram.showcase_bot.send_telegram_text", sent2
        ), patch("app.telegram.showcase_bot.answer_callback_query", AsyncMock()), patch(
            "app.telegram.showcase_bot.support_targets", AsyncMock(return_value={"admin": 5035800725})
        ), patch("app.academy.webinars.lead_card", AsyncMock(return_value="")), patch(
            "app.db.tenant_connection", lambda tenant_id: _AsyncNull()
        ), patch("app.jobs.outbox.enqueue_outbox_event", AsyncMock(return_value={"created": True})):
            await process_core_telegram_update(whieda_tenant, _tap("wwhy:ig_web24:price"), "t", binding=almira_binding)
    assert sent2.await_args.kwargs["text"].startswith("Понимаю про цену")


class _AsyncNull:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_second_press_answers_again_but_does_not_alert_again(whieda_tenant, almira_binding):
    # Алерт — одна строка outbox на человека, вебинар и причину: повторная вставка с тем же ключом created=False.
    again = AsyncMock(return_value={"created": False})
    result, sent, _, enqueue, _ = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:think", enqueue=again)
    assert result["admin_alert"] is False
    assert sent.await_args.kwargs["text"] == "Конечно, подумайте."
    assert enqueue.await_args.kwargs["idempotency_key"] == "wwhy:almira:ig_web24:think:301"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["wwhy:ig_web24:nope", "wwhy:ig_web24:", "wwhy:", "wwhy:ig_web24:PRICE", "wwhy:ig_web24:bad code"])
async def test_unknown_reason_is_answered_silently(whieda_tenant, almira_binding, data):
    result, sent, answered, enqueue, opened = await _press(whieda_tenant, almira_binding, data)
    assert (result["route"], result["known"]) == ("showcase_why", False)
    answered.assert_awaited_once()
    sent.assert_not_called()
    enqueue.assert_not_called()
    opened.assert_not_called()


@pytest.mark.asyncio
async def test_no_why_setting_at_all_is_a_silent_answer_too(whieda_tenant, almira_binding):
    bare = replace(SHOWCASE, support={k: v for k, v in SHOWCASE.support.items() if k != "why"})
    result, sent, answered, enqueue, _ = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:price", showcase=bare)
    assert result["known"] is False
    answered.assert_awaited_once()
    sent.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_known_reason_without_an_answer_still_tells_the_admin_and_the_person_gets_only_the_ack(whieda_tenant, almira_binding):
    result, sent, answered, enqueue, _ = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:mute")
    assert (result["known"], result["admin_alert"]) == (True, True)
    answered.assert_awaited_once()
    sent.assert_not_called()
    assert "«Без ответа»" in enqueue.await_args.kwargs["payload"]["text"]
    assert "📝" not in enqueue.await_args.kwargs["payload"]["text"]


@pytest.mark.asyncio
async def test_reason_buttons_work_in_a_private_chat_only(whieda_tenant, almira_binding):
    result, sent, _, enqueue, _ = await _press(whieda_tenant, almira_binding, "wwhy:ig_web24:think", chat_type="supergroup")
    assert result["route"] == "showcase_ignored_group"
    sent.assert_not_called()
    enqueue.assert_not_called()


def test_due_is_a_plain_frozen_record_with_safe_defaults():
    due = Due("hour", at(3, 18, 30), "т", None)
    assert (due.photos, due.video_note, due.audience, due.grant, due.login) == ((), "", "", None, False)
    assert Warmup("p1", timedelta(minutes=60), "т").grant is None and Webinar("x", at(3, 19, 30), "3", "").video_notes == ()
