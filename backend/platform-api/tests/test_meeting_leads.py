"""Запись на Живую встречу (kind = meeting, V44): разбор тела, выбор владельца, тексты, маршрут.

Без базы: сохранение, владелец по телефону в реальном SQL и отметка «открыл бота» —
в tests/test_meeting_leads_postgres.py.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.leads.meeting_leads import (
    card_note,
    card_tags,
    clean_messenger,
    clean_source_tag,
    clean_utm,
    is_test_page,
    meeting_problem,
    meeting_when,
    normalize_meeting_phone,
    origin_text,
    parse_meeting_id,
    partner_message_text,
    pick_person_anchor,
    plain_no_links,
)
from app.leads.service import parse_lead_body

UTC = dt.timezone.utc
# 05.11.2026 16:30 UTC = чт 05.11 19:30 МСК
STARTS = dt.datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _body(**extra) -> dict:
    return {
        "kind": "meeting",
        "name": "Анна Петрова",
        "phone": "+7 (999) 123-45-67",
        "messenger": "telegram",
        "consent": {"accepted": True, "version": "wwc-consent-2026-10"},
        "idempotency_key": "meet-1",
        **extra,
    }


# ---- телефон -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["+7 (999) 123-45-67", "8 999 123-45-67", "9991234567", "+79991234567", "7.999.123.45.67", "＋７ ９９９ １２３ ４５ ６７"],
)
def test_phone_is_normalized_to_e164(raw):
    assert normalize_meeting_phone(raw) == "+79991234567"


def test_foreign_number_keeps_its_digits():
    assert normalize_meeting_phone("+375 29 123-45-67") == "+375291234567"


@pytest.mark.parametrize("raw", ["+0 123 456 78 90", "+0123456789", "0123456789", "+00 123 456 7890", "0 000 000 00 00"])
def test_number_with_a_leading_zero_country_code_is_refused(raw):
    # общий E164_RE CRM «+0…» пропускает; для записи на встречу первая цифра после «+» — 1…9
    assert normalize_meeting_phone(raw) is None


def test_zero_start_phone_gives_422_in_the_meeting_body():
    with pytest.raises(HTTPException) as caught:
        parse_lead_body(_body(phone="+0 123 456 78 90"), "whieda")
    assert caught.value.status_code == 422 and caught.value.detail["errors"] == ["phone"]


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", "123", "12345", "звоните 89991234567 вечером", "mail@x.ru 89991234567", "+7 999 123-45-67 доб. 12", "1" * 16],
)
def test_not_a_phone_is_refused(raw):
    assert normalize_meeting_phone(raw) is None


# ---- остальные поля ----------------------------------------------------------------------------


def test_messenger_only_telegram_or_max():
    assert clean_messenger("Telegram") == "telegram"
    assert clean_messenger(" MAX ") == "max"
    for bad in (None, "", "whatsapp", "viber", "sms", 5):
        assert clean_messenger(bad) is None


def test_utm_keeps_five_strings_and_drops_the_rest():
    raw = {
        "source": "instagram",
        "medium": "x" * 300,
        "campaign": "  oct   live ",
        "content": 12,
        "term": "",
        "evil": "drop me",
        "utm_term": "ignored when empty term",
    }
    assert clean_utm(raw) == {
        "source": "instagram",
        "medium": "x" * 200,
        "campaign": "oct live",
        "content": "12",
        "term": "ignored when empty term",
    }
    assert clean_utm({"source": ["a"], "medium": None, "campaign": True}) == {}
    assert clean_utm("source=a") == {}
    assert clean_utm(None) == {}


def test_source_tag_rules():
    assert clean_source_tag("Reels_Oct-30") == "reels_oct-30"
    assert clean_source_tag("a" * 64) == "a" * 64
    for bad in ("a" * 65, "две слова", "a b", "tag!", "", None, "../x"):
        assert clean_source_tag(bad) is None


def test_meeting_id_parse():
    assert parse_meeting_id(7) == 7
    assert parse_meeting_id("12") == 12
    assert parse_meeting_id(" 12 ") == 12
    for bad in (None, "", 0, -3, "-3", True, False, "1.5", "abc", 2**63, [1], {"id": 1}, "١٢"):
        assert parse_meeting_id(bad) is None


def test_meeting_problem_reasons():
    future = {"kind": "live", "status": "planned", "starts_at": NOW + dt.timedelta(days=1)}
    assert meeting_problem(future, NOW) is None
    assert meeting_problem(None, NOW) == "not_found"
    assert meeting_problem({**future, "kind": "consult"}, NOW) == "not_live"
    assert meeting_problem({**future, "status": "cancelled"}, NOW) == "not_planned"
    assert meeting_problem({**future, "status": "done"}, NOW) == "not_planned"
    assert meeting_problem({**future, "starts_at": NOW - dt.timedelta(minutes=1)}, NOW) == "in_past"
    assert meeting_problem({**future, "starts_at": NOW}, NOW) == "in_past"


# ---- владелец «первый партнёр навсегда» --------------------------------------------------------


def _row(lead_id, owner, *, opened=True, active=True):
    """Прежняя заявка человека как её отдаёт find_person_anchor: бот открыт, владелец — действующий партнёр."""
    return {"lead_id": lead_id, "assigned_owner_id": owner, "bot_opened": opened, "owner_active": active}


def test_no_earlier_leads_means_no_anchor():
    assert pick_person_anchor([], "viktor") == (None, None)


def test_first_partner_stays_the_owner():
    rows = [_row("L1", "elena"), _row("L2", "anna")]
    assert pick_person_anchor(rows, "viktor") == ("L1", "elena")


def test_organic_first_lead_does_not_lock_the_person():
    rows = [_row("L1", "viktor"), _row("L2", "anna"), _row("L3", "elena")]
    # человек привязан к первой заявке, а владелец — первый партнёр: anna
    assert pick_person_anchor(rows, "viktor") == ("L1", "anna")
    assert pick_person_anchor(rows[:1], "viktor") == ("L1", None)


def test_rows_without_owner_are_skipped():
    rows = [_row("L1", None), _row("L2", "anna")]
    assert pick_person_anchor(rows, "viktor") == ("L1", "anna")


def test_a_lead_whose_bot_was_never_opened_is_not_an_anchor():
    # номер из формы сайта чужой человек мог вписать в заявку партнёра: без бота номер за партнёром не закрепляется
    rows = [_row("L1", "elena", opened=False)]
    assert pick_person_anchor(rows, "viktor") == ("L1", None)
    # якорем становится первая заявка, по которой бот открыт, а не просто первая с партнёром
    rows = [_row("L1", "elena", opened=False), _row("L2", "anna"), _row("L3", "elena")]
    assert pick_person_anchor(rows, "viktor") == ("L1", "anna")


def test_a_partner_without_an_active_subscription_is_not_an_anchor():
    # ушедший партнёр номер не держит: иначе notify_partner молча терял бы сообщения
    rows = [_row("L1", "elena", active=False)]
    assert pick_person_anchor(rows, "viktor") == ("L1", None)
    rows = [_row("L1", "elena", active=False), _row("L2", "anna")]
    assert pick_person_anchor(rows, "viktor") == ("L1", "anna")


def test_missing_markers_count_as_no_anchor():
    # строка без bot_opened/owner_active (старый формат) подтверждённой не считается
    rows = [{"lead_id": "L1", "assigned_owner_id": "elena"}]
    assert pick_person_anchor(rows, "viktor") == ("L1", None)


def test_organic_owner_is_never_an_anchor_even_when_confirmed():
    assert pick_person_anchor([_row("L1", "viktor")], "viktor") == ("L1", None)


# ---- разбор тела запроса -----------------------------------------------------------------------


def test_valid_meeting_body_becomes_lead_input():
    lead = parse_lead_body(
        _body(
            meeting_id=15,
            marketing_consent=True,
            utm={"source": "instagram", "campaign": "oct", "junk": "x"},
            source_tag="Reels_Oct",
            page_url="https://elena.wwc.best/vstrecha/?x=1",
            landing_url="https://wwc.best/vstrecha/",
            first_ref="onlineelena",
            active_ref="onlineelena",
        ),
        "whieda",
    )
    assert lead.kind == "meeting"
    assert lead.name == "Анна Петрова"
    assert lead.phone_e164 == "+79991234567"
    assert lead.contact == "+79991234567"
    assert lead.messenger == "telegram"
    assert lead.meeting_id == 15
    assert lead.marketing_consent is True
    assert lead.utm == {"source": "instagram", "campaign": "oct"}
    assert lead.source_tag == "reels_oct"
    assert lead.product_name == "Живая встреча"
    assert lead.consent_version == "wwc-consent-2026-10"
    assert lead.first_ref_code == "onlineelena"
    assert lead.active_ref_code == "onlineelena"
    assert lead.idempotency_key == "meet-1"
    assert lead.metadata["landing_url"] == "https://wwc.best/vstrecha/"


def test_refs_follow_the_usual_rules_for_meeting():
    # как у заявок: ссылка на поддомене партнёра задаёт и первое касание, и активный ref
    lead = parse_lead_body(_body(page_url="https://anna.wwc.best/vstrecha/"), "whieda")
    assert (lead.first_ref_code, lead.active_ref_code) == ("anna", "anna")
    lead = parse_lead_body(_body(first_ref="Elena", active_ref="Anna"), "whieda")
    assert (lead.first_ref_code, lead.active_ref_code) == ("elena", "anna")


def test_defaults_for_the_optional_fields():
    lead = parse_lead_body(_body(consent={"accepted": "true"}), "whieda")
    assert lead.meeting_id is None
    assert lead.marketing_consent is False
    assert lead.utm == {}
    assert lead.source_tag is None
    assert lead.consent_version  # версия по умолчанию из настроек
    assert lead.first_ref_code == "" and lead.active_ref_code == ""


def _errors(body: dict) -> tuple[int, list[str]]:
    with pytest.raises(HTTPException) as caught:
        parse_lead_body(body, "whieda")
    return caught.value.status_code, caught.value.detail["errors"]


def test_each_required_field_gives_422():
    assert _errors(_body(name="   ")) == (422, ["name"])
    assert _errors(_body(phone="не телефон")) == (422, ["phone"])
    assert _errors(_body(phone=None)) == (422, ["phone"])
    assert _errors(_body(messenger="whatsapp")) == (422, ["messenger"])
    assert _errors(_body(messenger=None)) == (422, ["messenger"])
    assert _errors(_body(consent={"accepted": False})) == (422, ["consent"])
    assert _errors(_body(consent={"version": "v1"})) == (422, ["consent"])
    assert _errors(_body(consent=True)) == (422, ["consent"])
    assert _errors(_body(consent=None)) == (422, ["consent"])
    assert _errors(_body(idempotency_key="")) == (422, ["idempotency_key"])


def test_all_wrong_fields_are_listed_together():
    status, errors = _errors({"kind": "meeting"})
    assert status == 422
    assert errors == ["name", "phone", "messenger", "consent", "idempotency_key"]


def test_name_is_cut_to_160():
    lead = parse_lead_body(_body(name="Я" * 400), "whieda")
    assert len(lead.name) == 160


def test_bad_optional_fields_are_dropped_with_a_note_not_refused():
    lead = parse_lead_body(
        _body(meeting_id="abc", source_tag="две слова", page_url="https://x.test/" + "a" * 2000, utm=["x"]),
        "whieda",
    )
    assert lead.meeting_id is None
    assert lead.metadata["meeting_id_invalid"] == "abc"
    assert lead.source_tag is None
    assert lead.metadata["source_tag_invalid"] == "две слова"
    assert len(lead.page_url) == 1000
    assert lead.utm == {}


def test_visitor_session_must_be_a_uuid():
    lead = parse_lead_body(_body(visitor_session_id="not-a-uuid"), "whieda")
    assert lead.visitor_session_id == ""
    uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert parse_lead_body(_body(visitor_session_id=uid), "whieda").visitor_session_id == uid


def test_owner_fields_and_honeypot_are_refused_for_meeting_too():
    with pytest.raises(HTTPException) as caught:
        parse_lead_body(_body(assigned_owner_id="evil"), "whieda")
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:
        parse_lead_body(_body(website="http://spam"), "whieda")
    assert caught.value.status_code == 400


def test_other_kinds_are_parsed_as_before():
    base = {"name": "Т", "contact": "+79990000000", "product": "Активатор", "idempotency_key": "k"}
    plain_lead = parse_lead_body(base, "whieda")
    assert plain_lead.kind == "request"
    assert plain_lead.phone_e164 is None and plain_lead.utm == {} and plain_lead.meeting_id is None
    # чужое значение kind не ломает прежнюю заявку и не включает разбор встречи
    assert parse_lead_body({**base, "kind": "order"}, "whieda").kind == "request"
    with pytest.raises(HTTPException) as caught:
        parse_lead_body({"name": "Т", "kind": "request", "idempotency_key": "k"}, "whieda")
    assert caught.value.status_code == 400


# ---- тексты ------------------------------------------------------------------------------------


def test_origin_text_variants():
    assert origin_text(ref_code="elena", source_tag="reels_oct", utm_source="instagram") == (
        "партнёр elena, метка reels_oct, источник instagram"
    )
    assert origin_text(source_tag="reels_oct") == "метка reels_oct"
    assert origin_text(utm_source="vk") == "источник vk"
    assert origin_text() == "сайт"


def test_partner_message_format():
    text = partner_message_text(
        name="Анна Петрова",
        phone_e164="+79991234567",
        messenger="telegram",
        starts_at=STARTS,
        origin="партнёр elena, источник instagram",
        now=NOW,
    )
    assert text.split("\n") == [
        "Новая запись на Живую встречу чт 05.11 19:30 МСК",
        "Имя: Анна Петрова",
        "Телефон: +79991234567",
        "Мессенджер: Telegram",
        "Откуда: партнёр elena, источник instagram",
    ]


def test_partner_message_without_meeting_and_with_max():
    text = partner_message_text(
        name="Борис", phone_e164="+79990000000", messenger="max", starts_at=None, origin="сайт", now=NOW
    )
    lines = text.split("\n")
    assert lines[0] == "Новая запись на Живую встречу"
    assert "Мессенджер: Max" in lines


def test_partner_message_neutralizes_html_in_the_name():
    text = partner_message_text(
        name='<b>Аня</b> <a href="http://evil">тут</a> & <script>',
        phone_e164="+79990000000",
        messenger="telegram",
        starts_at=None,
        origin=origin_text(source_tag="t"),
        now=NOW,
    )
    assert "<" not in text and ">" not in text
    assert "‹b›Аня‹/b›" in text
    # перевод строки в имени не ломает формат сообщения
    multi = partner_message_text(
        name="Аня\nТелефон: +70000000000", phone_e164="+79990000000", messenger="max", starts_at=None, origin="сайт"
    )
    assert multi.count("\nТелефон:") == 1


@pytest.mark.parametrize(("raw", "expected"), [
    ("Аня https://evil.example/x?y=1 привет", "Аня … привет"),
    ("Аня HTTP://EVIL.example", "Аня …"),
    ("смотри www.evil.example/a и всё", "смотри … и всё"),
    ("ищи t.me/evil_channel или T.ME/Other", "ищи … или …"),
    ("telegram.me/evil", "…"),
    ("пиши @evil_user, а лучше @другой", "пиши …, а лучше …"),
    ("Анна Петрова", "Анна Петрова"),
    ("Анна-Мария О'Нил & Co", "Анна-Мария О'Нил & Co"),   # & экранирует отправка, здесь он остаётся
])
def test_links_and_mentions_are_cut_out(raw, expected):
    assert plain_no_links(raw) == expected


def test_plain_no_links_keeps_the_html_brackets_neutralised_and_the_limit():
    assert plain_no_links("<b>Аня</b>") == "‹b›Аня‹/b›"
    assert len(plain_no_links("я" * 500, 160)) == 160
    assert plain_no_links("https://" + "a" * 500 + " " + "б" * 10, 160) == "… " + "б" * 10
    assert plain_no_links(None) == ""


def test_partner_message_has_no_links_or_mentions_from_the_name():
    text = partner_message_text(
        name="Аня https://evil.example/pay @scammer t.me/evil www.evil.example",
        phone_e164="+79990000000",
        messenger="telegram",
        starts_at=None,
        origin=origin_text(ref_code="elena", source_tag="t", utm_source="https://evil.example/x @evil"),
        now=NOW,
    )
    assert "http" not in text and "@" not in text and "t.me" not in text and "www." not in text
    assert text.split("\n")[1] == "Имя: Аня … … … …"
    assert text.split("\n")[-1] == "Откуда: партнёр elena, метка t, источник … …"


def test_origin_text_cuts_links_out_of_the_ref_code_tag_and_utm_source():
    assert origin_text(ref_code="https://evil.example", source_tag="@evil", utm_source="t.me/evil") == (
        "партнёр …, метка …, источник …"
    )
    # то же и у уже готового текста «Откуда»: сообщение партнёру защищено на выходе
    text = partner_message_text(
        name="Борис", phone_e164="+79990000000", messenger=None, starts_at=None,
        origin="партнёр x, источник https://evil.example @evil", now=NOW,
    )
    assert text.split("\n")[-1] == "Откуда: партнёр x, источник … …"


def test_card_note_and_tags():
    note = card_note(
        phone_e164="+79991234567", messenger="max", starts_at=STARTS, origin="метка reels_oct", now=NOW
    )
    assert note.split("\n") == [
        "Запись на Живую встречу.",
        "Телефон: +79991234567",
        "Мессенджер: Max",
        "Встреча: чт 05.11 19:30 МСК",
        "Откуда: метка reels_oct",
    ]
    assert card_tags(STARTS) == ["записан 05.11", "бот не открыл"]
    assert card_tags(None) == ["бот не открыл"]
    # без выбранной встречи строки «Встреча» нет
    assert "Встреча:" not in card_note(phone_e164="+79991234567", messenger=None, starts_at=None, origin="сайт")


def test_meeting_when_uses_moscow_time_and_adds_year_for_another_year():
    assert meeting_when(STARTS, now=NOW) == "чт 05.11 19:30 МСК"
    assert meeting_when(None) == ""
    # 23:30 UTC в МСК уже следующие сутки
    late = dt.datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
    assert meeting_when(late, now=NOW) == "пт 06.11 02:30 МСК"


def test_test_pages_do_not_notify():
    assert is_test_page("https://staging.wwc.best/vstrecha/")
    assert is_test_page("https://cabinet.staging.wwc.best/")
    assert is_test_page("https://admin.wwc.best/")
    assert not is_test_page("https://elena.wwc.best/vstrecha/")
    assert not is_test_page("")
    assert not is_test_page(None)


# ---- маршрут POST /api/v1/leads ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_returns_public_id_for_meeting(client, monkeypatch):
    saved = []

    async def fake_save(lead):
        saved.append(lead)
        return {"public_id": "L-20261030-ABC1234", "created": True, "lead_id": "00000000-0000-0000-0000-000000000001"}

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)
    response = await client.post("/api/v1/leads", json=_body(), headers={"host": "wwc.best"})
    assert response.status_code == 201
    assert response.json() == {"ok": True, "public_id": "L-20261030-ABC1234"}
    assert saved[0].kind == "meeting"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "shadow"])
async def test_meeting_is_saved_by_core_even_in_legacy_and_shadow_modes(client, monkeypatch, mode):
    saved = []

    async def fake_save(lead):
        saved.append(lead.kind)
        return {"public_id": "L-X", "created": True, "lead_id": "00000000-0000-0000-0000-000000000002"}

    async def must_not_run(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("legacy proxy or shadow check ran for a meeting lead")

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)
    monkeypatch.setattr("app.leads.routes.validate_lead_shadow", must_not_run)
    monkeypatch.setattr("app.advisor.legacy_adapter.post_legacy_json", must_not_run)
    monkeypatch.setattr("app.leads.routes.get_settings", lambda: SimpleNamespace(core_route_leads=mode))
    response = await client.post("/api/v1/leads", json=_body(), headers={"host": "wwc.best"})
    assert response.status_code == 201
    assert response.json()["public_id"] == "L-X"
    assert saved == ["meeting"]


@pytest.mark.asyncio
async def test_route_answers_422_with_the_wrong_fields(client, monkeypatch):
    async def never(lead):  # pragma: no cover - failure path
        raise AssertionError("an invalid meeting lead must not be saved")

    monkeypatch.setattr("app.leads.routes.save_lead", never)
    response = await client.post(
        "/api/v1/leads",
        json=_body(phone="нет", consent={"accepted": False}),
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["errors"] == ["phone", "consent"]


@pytest.mark.asyncio
async def test_old_requests_still_answer_with_the_old_body(client, monkeypatch):
    async def fake_save(lead):
        return {"public_id": "L-OLD", "created": True, "lead_id": "00000000-0000-0000-0000-000000000003"}

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)
    response = await client.post(
        "/api/v1/leads",
        json={"name": "Т", "contact": "+79990000000", "product": "Активатор", "idempotency_key": "k-old"},
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 201
    assert response.json() == {"ok": True, "message": "Заявка принята. Мы скоро свяжемся с вами."}
