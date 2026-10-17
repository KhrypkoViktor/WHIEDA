"""Ритм «Онлайн-старта»: серия, тексты карточки и табло, расписание, кнопки бота (17.10.2026).

Тексты карточки и табло — контракт из Obsidian Online_start/RITM_resheniye.md §2–3.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.academy import rhythm
from app.telegram import rhythm as bot_rhythm
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage

OWNER = 777
D = date(2026, 10, 18)  # воскресенье


def days(*offsets: int) -> set[date]:
    return {D - timedelta(days=n) for n in offsets}


def test_streak_ends_today_or_yesterday_and_breaks_on_a_gap():
    assert rhythm.streak(set(), D) == 0
    assert rhythm.streak(days(0), D) == 1
    assert rhythm.streak(days(0, 1, 2), D) == 3
    # Сегодня ещё не засчитан — серия кончается вчера и не обнуляется до конца дня.
    assert rhythm.streak(days(1, 2, 3), D) == 3
    # Пропуск позавчера: серия — только то, что после пропуска.
    assert rhythm.streak(days(0, 1, 3, 4, 5), D) == 2
    # Вчера пропущен, сегодня ещё нет — серия 0.
    assert rhythm.streak(days(2, 3), D) == 0
    assert rhythm.streak(days(0, 2, 3), D) == 1


def test_russian_dates_and_plurals():
    assert rhythm.ru_date(D) == "18 октября"
    assert rhythm.ru_date(date(2026, 1, 1)) == "1 января"
    words = [rhythm.plural(n, "день", "дня", "дней") for n in (1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 111)]
    assert words == ["день", "дня", "дня", "дней", "дней", "дней", "дней", "день", "дня", "дней", "дней"]
    assert rhythm.streak_phrase(3, broken=False) == "Серия: 3 дня подряд"
    assert rhythm.streak_phrase(1, broken=True) == "Серия: 1 день подряд"
    assert rhythm.streak_phrase(0, broken=False) == "Серия пока не началась"
    assert rhythm.streak_phrase(0, broken=True) == "Серия: начнём заново"


def test_card_text_is_the_decision_doc_text():
    lesson = {"title": "Самое сложное — начать", "minutes": 5}
    assert rhythm.card_text(D, {}, 3, broken=False, lesson=lesson) == (
        "Сегодня, 18 октября. Серия: 3 дня подряд.\n"
        "☐ Пост или видео\n"
        "☐ Один человек: написать, позвонить, пригласить\n"
        "☐ Пятёрка: комментарий и сохранение у четверых\n"
        "📖 Урок дня: «Самое сложное — начать», 5 минут"
    )
    marked = rhythm.card_text(D, {"post": True, "five": True}, 0, broken=True, lesson=None)
    assert marked.splitlines() == [
        "Сегодня, 18 октября. Серия: начнём заново.",
        "✅ Пост или видео",
        "☐ Один человек: написать, позвонить, пригласить",
        "✅ Пятёрка: комментарий и сохранение у четверых",
    ]
    assert rhythm.card_text(D, {}, 0, broken=False, lesson={"title": "Т", "minutes": None}).endswith("📖 Урок дня: «Т»")
    assert rhythm.lesson_line({"title": "Т", "minutes": 1}) == "📖 Урок дня: «Т», 1 минута"
    assert rhythm.lesson_line({"title": "Т", "minutes": 3}) == "📖 Урок дня: «Т», 3 минуты"


def test_card_buttons_toggle_three_marks_and_open_the_lesson():
    keyboard = rhythm.card_keyboard(D, {"person": True}, "https://wwc.best/academy/?course=c&lesson=l")
    toggles, lesson = keyboard["inline_keyboard"]
    assert [b["text"] for b in toggles] == ["☐ Пост", "✅ Человек", "☐ Пятёрка"]
    assert [b["callback_data"] for b in toggles] == ["rh:p:20261018", "rh:h:20261018", "rh:f:20261018"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in toggles)
    assert lesson == [{"text": "📖 Урок дня", "url": "https://wwc.best/academy/?course=c&lesson=l"}]
    assert len(rhythm.card_keyboard(D, {}, None)["inline_keyboard"]) == 1


def test_board_text_is_the_decision_doc_text():
    people = [
        {"name": "Ольга", "days": 4, "streak": 0},
        {"name": "Марина", "days": 7, "streak": 7},
        {"name": "Пётр", "days": 0, "streak": 0},
        {"name": "Сергей", "days": 6, "streak": 2},
    ]
    assert rhythm.board_text(1, people) == (
        "Неделя 1. Кто в ритме:\n"
        "Марина — 7 из 7, серия 7\n"
        "Сергей — 6 из 7\n"
        "Ольга — 4 из 7"
    )
    # Серия от трёх; при равных днях — у кого серия длиннее, потом по имени.
    tied = [{"name": "Яна", "days": 3, "streak": 3}, {"name": "Аня", "days": 3, "streak": 3}, {"name": "Вера", "days": 3, "streak": 5}]
    assert rhythm.board_lines(tied) == ["Вера — 3 из 7, серия 5", "Аня — 3 из 7, серия 3", "Яна — 3 из 7, серия 3"]
    many = [{"name": f"Имя{n:02d}", "days": 1 + n % 7, "streak": 0} for n in range(30)]
    assert len(rhythm.board_lines(many)) == 20
    assert rhythm.board_text(2, [{"name": "Пётр", "days": 0, "streak": 0}]) is None


def test_week_numbers_and_first_names():
    assert rhythm.week_of(date(2026, 10, 17)) == (date(2026, 10, 12), D)
    assert rhythm.week_number(D, None) == 1
    assert rhythm.week_number(D, date(2026, 10, 17)) == 1
    assert rhythm.week_number(date(2026, 10, 25), date(2026, 10, 17)) == 2
    assert rhythm.week_number(date(2026, 10, 25), date(2026, 10, 19)) == 1
    assert rhythm.week_number(date(2026, 10, 11), date(2026, 10, 19)) == 1
    assert rhythm.first_name("", "Марина Иванова") == "Марина"
    assert rhythm.first_name("Оля", "Ольга Петрова") == "Оля"
    assert rhythm.first_name(None, "  ") == "Участник"


def test_settings_default_and_bad_values(monkeypatch):
    from app.settings import get_settings

    for name in ("PLATFORM_RHYTHM_ENABLED", "PLATFORM_RHYTHM_COURSE", "PLATFORM_RHYTHM_TIMEZONE",
                 "PLATFORM_RHYTHM_CARD_TIME", "PLATFORM_RHYTHM_BOARD_TIME", "PLATFORM_RHYTHM_WEEK1_START"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.platform_rhythm_enabled is False and settings.platform_rhythm_course == "online-start-4w"
    assert (rhythm.card_time().isoformat(), rhythm.board_time().isoformat()) == ("09:00:00", "20:00:00")
    assert rhythm.zone().key == "Europe/Minsk" and rhythm.week1_start() is None
    # 21:30 UTC субботы — уже воскресенье в Минске (UTC+3).
    assert rhythm.local_today(datetime(2026, 10, 17, 21, 30, tzinfo=timezone.utc)) == D
    monkeypatch.setenv("PLATFORM_RHYTHM_CARD_TIME", "9 утра")
    monkeypatch.setenv("PLATFORM_RHYTHM_TIMEZONE", "Europe/Nowhere")
    monkeypatch.setenv("PLATFORM_RHYTHM_WEEK1_START", "2026-10-19")
    get_settings.cache_clear()
    assert rhythm.card_time().isoformat() == "09:00:00" and rhythm.zone().key == "Europe/Minsk"
    assert rhythm.week1_start() == date(2026, 10, 19)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_schedule_cards_in_the_morning_board_on_sunday_evening(monkeypatch, whieda_bot_binding):
    from app.settings import get_settings

    bindings = {"whieda": whieda_bot_binding}
    minsk = lambda d, h, m: datetime(2026, 10, d, h - 3, m, tzinfo=timezone.utc)  # noqa: E731 — Минск UTC+3
    cards, boards = AsyncMock(return_value=2), AsyncMock(return_value=1)
    with patch.object(bot_rhythm, "enqueue_rhythm_cards", cards), patch.object(bot_rhythm, "post_weekly_boards", boards):
        monkeypatch.delenv("PLATFORM_RHYTHM_ENABLED", raising=False)
        get_settings.cache_clear()
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 9, 5)) == {}
        monkeypatch.setenv("PLATFORM_RHYTHM_ENABLED", "true")
        get_settings.cache_clear()
        assert await bot_rhythm.rhythm_step({}, minsk(18, 9, 5)) == {}
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 8, 59)) == {}
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 9, 0)) == {"rhythm_cards": 2}
        assert cards.await_args.args == (bindings, D)
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 9, 59)) == {"rhythm_cards": 2}
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 10, 0)) == {}
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 20, 10)) == {"rhythm_boards": 1}
        assert boards.await_args.args == (bindings, D)
        assert await bot_rhythm.rhythm_step(bindings, minsk(18, 20, 45)) == {}
        assert await bot_rhythm.rhythm_step(bindings, minsk(17, 20, 10)) == {}  # суббота
    get_settings.cache_clear()


def press(data: str, *, user_id: int = 9001, chat_type: str = "private") -> TelegramCallbackQuery:
    raw = {"callback_query": {"message": {"message_id": 55, "chat": {"id": user_id}}}}
    return TelegramCallbackQuery(
        chat_id=user_id, user_id=user_id, callback_query_id="cb", data=data, chat_type=chat_type, raw=raw
    )


@pytest.fixture
def bot(whieda_bot_binding):
    mocks = {
        "answer": AsyncMock(return_value={"ok": True}),
        "call": AsyncMock(return_value={"ok": True, "result": {"message_id": 55}}),
        "send": AsyncMock(return_value={"ok": True}),
        "member": AsyncMock(return_value=True),
        "toggle": AsyncMock(return_value={"post": True, "person": True, "five": True, "done_at": datetime.now(timezone.utc)}),
        "state": AsyncMock(return_value={"marks": {"post": True, "person": True, "five": True}, "streak": 4, "broken": False}),
        "lesson": AsyncMock(return_value={"slug": "l", "title": "Самое сложное — начать", "minutes": 5,
                                          "url": "https://wwc.best/academy/?course=online-start-4w&lesson=l"}),
    }
    with binding_context_scope(whieda_bot_binding), \
         patch.object(bot_rhythm, "answer_callback_query", mocks["answer"]), \
         patch.object(bot_rhythm, "_call_telegram", mocks["call"]), \
         patch.object(bot_rhythm, "send_telegram_text", mocks["send"]), \
         patch.object(bot_rhythm, "lesson_of_day", mocks["lesson"]), \
         patch.object(bot_rhythm, "with_site_login", AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=t")), \
         patch.object(bot_rhythm.rhythm, "is_member", mocks["member"]), \
         patch.object(bot_rhythm.rhythm, "toggle_mark", mocks["toggle"]), \
         patch.object(bot_rhythm.rhythm, "card_state", mocks["state"]), \
         patch.object(bot_rhythm.rhythm, "local_today", lambda now=None: D):
        yield mocks


@pytest.mark.asyncio
async def test_a_tap_toggles_the_mark_and_edits_the_same_card(whieda_tenant, bot):
    result = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:h:20261018"), trace_id="t")
    assert result["status"] == "day_done"
    assert bot["toggle"].await_args.args == ("whieda", 9001, D, "person")
    assert bot["answer"].await_args.kwargs["text"] == "День засчитан ✅"
    method, payload = bot["call"].await_args.args
    assert method == "editMessageText" and payload["message_id"] == 55 and payload["chat_id"] == 9001
    assert payload["text"].startswith("Сегодня, 18 октября. Серия: 4 дня подряд.\n✅ Пост или видео")
    toggles, lesson = payload["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in toggles] == ["✅ Пост", "✅ Человек", "✅ Пятёрка"]
    assert lesson[0]["url"].endswith("lesson=l#wwc-login=t")
    # Вчерашняя карточка после полуночи — тоже правится.
    bot["toggle"].return_value = {"post": False, "person": True, "five": True, "done_at": None}
    result = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261017"), trace_id="t")
    assert result["status"] == "toggled" and bot["answer"].await_args.kwargs["text"] is None
    assert bot["toggle"].await_args.args[2] == date(2026, 10, 17)


@pytest.mark.asyncio
async def test_old_cards_strangers_and_groups_change_nothing(whieda_tenant, bot):
    old = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261016"), trace_id="t")
    assert old["status"] == "stale_card" and bot["answer"].await_args.kwargs["text"] == bot_rhythm.STALE_TEXT
    future = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261019"), trace_id="t")
    assert future["status"] == "stale_card"
    bad = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261340"), trace_id="t")
    assert bad["status"] == "bad_day"
    group = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261018", chat_type="supergroup"), trace_id="t")
    assert group["status"] == "private_chat_required"
    bot["member"].return_value = False
    stranger = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("rh:p:20261018"), trace_id="t")
    assert stranger["status"] == "not_member" and bot["answer"].await_args.kwargs["text"] == bot_rhythm.NOT_MEMBER_TEXT
    assert bot["toggle"].await_count == 0 and bot["call"].await_count == 0
    assert await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, press("acad:home"), trace_id="t") is None


def message(text: str, user_id: int) -> TelegramMessage:
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type="private", file_id=None, raw={})


@pytest.mark.asyncio
async def test_owner_commands_card_now_and_board_now(monkeypatch, whieda_tenant, bot):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(OWNER))
    get_settings.cache_clear()
    # Не владельцу «ритм» и «табло» — обычные слова: идут дальше (советник).
    assert await bot_rhythm.try_handle_rhythm_text(whieda_tenant, message("ритм", 9001), trace_id="t") is None
    assert await bot_rhythm.try_handle_rhythm_text(whieda_tenant, message("ритм жизни", OWNER), trace_id="t") is None
    card = await bot_rhythm.try_handle_rhythm_text(whieda_tenant, message("Ритм", OWNER), trace_id="t")
    assert card["status"] == "sent"
    sent = bot["send"].await_args.kwargs
    assert sent["chat_id"] == str(OWNER) and sent["text"].startswith("Сегодня, 18 октября. Серия: 4 дня подряд.")
    assert sent["reply_markup"]["inline_keyboard"][1][0]["url"].endswith("#wwc-login=t")
    publish = AsyncMock(return_value={"status": "posted", "text": "Неделя 1. Кто в ритме:\nМарина — 7 из 7, серия 7"})
    with patch.object(bot_rhythm, "publish_board", publish):
        board = await bot_rhythm.try_handle_rhythm_text(whieda_tenant, message("/табло", OWNER), trace_id="t")
        assert board["status"] == "posted" and publish.await_args.args == ("whieda", D)
        assert bot["send"].await_args.kwargs["text"].startswith("Табло отправлено в группу курса:\n\nНеделя 1.")
        publish.return_value = {"status": "empty"}
        empty = await bot_rhythm.try_handle_rhythm_text(whieda_tenant, message("табло", OWNER), trace_id="t")
        assert empty["status"] == "empty" and bot["send"].await_args.kwargs["text"] == bot_rhythm.BOARD_REPLY["empty"]
    get_settings.cache_clear()
