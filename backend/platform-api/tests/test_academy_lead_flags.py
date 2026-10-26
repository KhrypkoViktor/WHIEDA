"""Флаги «в боте» для таблицы заявок (18.10.2026): GET …/academy/showcase/lead-flags?key=…"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.academy.ad_leads import Opened, flags_key, lead_flags, parse_leads
from app.academy.showcases import Showcase

URL = "/api/v1/content-access/academy/showcase/lead-flags"
HEADERS = {"x-forwarded-host": "wwc.best"}
SHEET = {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657"}
KEY = "k3y-for-tests-only"

HEADER = (
    "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,form_name,is_organic,"
    "platform,что_интересует,учились_в_almira_раньше?,ваш_ник_в_telegram_(для_ссылки_на_эфир),полное_имя,"
    "номер_телефона,lead_status,Статус,Комментарий"
)
CSV = "\n".join([
    HEADER,
    "l:111,2026-10-16T08:40:11-05:00,ag:1,A3,as:2,A,c:3,Композиция,f:4,Вебинар,false,ig,"
    "бесплатный_вебинар_24_октября,нет,t.me/kvetka_space,Анна Петрова,p:+375291112233,CREATED,,",
    "l:222,2026-10-16T08:54:16-05:00,ag:1,A3,as:2,A,c:3,Композиция,f:4,Вебинар,false,fb,"
    "бесплатный_вебинар_24_октября,да,,Мария Иванова,p:+375294445566,CREATED,,",
    "l:333,2026-10-16T09:10:00-05:00,ag:1,A3,as:2,A,c:3,Композиция,f:4,Вебинар,false,ig,"
    "бесплатный_вебинар_24_октября,нет,@olga_k,Ольга Козлова,p:+375297778899,CREATED,,",
])

ALMIRA = Showcase(
    tenant_id="whieda",
    showcase_id="almira",
    is_default=False,
    hosts=("academy.almira.by",),
    owner_actor_id="almira",
    brand_name="ALMIRA",
    academy_title="Онлайн-академия ALMIRA",
    support={"leads_sheet": {**SHEET, "since": "2026-10-16T17:30:00+03:00", "flags_key": KEY}},
)

# 111 — по строке таблицы, сопоставленной при /start; 333 — по нику; 222 — не открывал.
OPENS = [
    Opened(telegram_user_id=301, username="anna_p", name="Anna", lead_id="111"),
    Opened(telegram_user_id=302, username="olga_k", name="Olga", lead_id=None),
]


@asynccontextmanager
async def _conn(_tenant_id):
    yield object()


def _patches(showcase: Showcase = ALMIRA, *, leads=None, opens=OPENS, fetch_error: Exception | None = None):
    fetch = AsyncMock(side_effect=fetch_error) if fetch_error else AsyncMock(return_value=parse_leads(CSV) if leads is None else leads)
    return (
        patch("app.academy.routes.showcase_for_request", AsyncMock(return_value=showcase)),
        patch("app.academy.ad_leads.fetch_leads", fetch),
        patch("app.academy.ad_leads.load_opens", AsyncMock(return_value=opens)),
        patch("app.db.tenant_connection", _conn),
    )


async def _get(client, showcase: Showcase = ALMIRA, *, key: str | None = KEY, **kwargs):
    p = _patches(showcase, **kwargs)
    with p[0] as resolve, p[1] as fetch, p[2] as opens, p[3]:
        response = await client.get(URL, params={} if key is None else {"key": key}, headers=HEADERS)
    return response, resolve, fetch, opens


@pytest.mark.asyncio
async def test_flags_split_lead_ids_into_in_bot_and_not_in_bot(client):
    response, resolve, fetch, opens = await _get(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["ok"] is True
    assert body["in_bot"] == ["111", "333"]
    assert body["not_in_bot"] == ["222"]
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() == timezone.utc.utcoffset(None)
    assert fetch.await_args.args[0]["id"] == SHEET["id"]
    assert opens.await_args.args[1:] == ("whieda", "almira")
    # Витрина — по хосту, без превью ?showcase=…
    assert resolve.await_args.kwargs.get("allow_preview", False) is False


@pytest.mark.asyncio
async def test_flags_carry_only_lead_ids_no_names_phones_or_nicks(client):
    response, *_ = await _get(client)
    # «На эфире» (24.10) и «Записан на» (26.10) — тоже только id и даты.
    assert set(response.json()) == {"ok", "updated_at", "in_bot", "not_in_bot", "joined", "booked"}
    for private in ("Анна", "Петрова", "Мария", "Ольга", "+375", "375291112233", "kvetka_space", "olga_k", "anna_p"):
        assert private not in response.text


@pytest.mark.asyncio
async def test_every_lead_row_goes_to_exactly_one_list(client):
    response, *_ = await _get(client)
    body = response.json()
    ids = {lead.lead_id for lead in parse_leads(CSV)}
    assert set(body["in_bot"]) | set(body["not_in_bot"]) == ids
    assert not set(body["in_bot"]) & set(body["not_in_bot"])


@pytest.mark.asyncio
async def test_no_opens_means_nobody_in_bot(client):
    response, *_ = await _get(client, opens=[])
    assert response.status_code == 200
    assert response.json()["in_bot"] == [] and response.json()["not_in_bot"] == ["111", "222", "333"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "support",
    [
        {},
        {"leads_sheet": SHEET},
        {"leads_sheet": {**SHEET, "flags_key": ""}},
        {"leads_sheet": {**SHEET, "flags_key": "   "}},
        {"leads_sheet": {**SHEET, "flags_key": 12345}},
        {"leads_sheet": {"id": "../etc", "gid": "1", "flags_key": KEY}},
    ],
)
async def test_404_when_flags_not_configured(client, support):
    response, _, fetch, _ = await _get(client, replace(ALMIRA, support=support))
    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "flags_not_configured"}
    assert response.headers["cache-control"] == "no-store"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "wrong", KEY + "x", KEY[:-1], "ключ"])
async def test_403_on_missing_or_wrong_key(client, key):
    response, _, fetch, opens = await _get(client, key=key)
    assert response.status_code == 403
    assert response.json() == {"ok": False, "error": "bad_key"}
    assert response.headers["cache-control"] == "no-store"
    fetch.assert_not_awaited()
    opens.assert_not_awaited()


@pytest.mark.asyncio
async def test_503_when_sheet_cannot_be_read(client):
    response, *_ = await _get(client, fetch_error=RuntimeError("leads_sheet_not_csv"))
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "sheet_unavailable"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_lead_id_in_bot_wins_over_duplicate_row():
    twin = replace(parse_leads(CSV)[1], lead_id="111")  # вторая строка с тем же id, но не открывавшая бота
    p = _patches(leads=[parse_leads(CSV)[0], twin])
    with p[0], p[1], p[2], p[3]:
        flags = await lead_flags("whieda", ALMIRA)
    assert flags["in_bot"] == ["111"] and flags["not_in_bot"] == []


def test_flags_key_needs_valid_sheet_and_string_key():
    assert flags_key(ALMIRA.support) == KEY
    assert flags_key({"leads_sheet": {**SHEET, "flags_key": f"  {KEY} "}}) == KEY
    assert flags_key({"leads_sheet": {"id": "x", "gid": "1", "flags_key": KEY}}) == ""
    assert flags_key({"leads_sheet": SHEET}) == "" and flags_key(None) == "" and flags_key({}) == ""


@pytest.mark.asyncio
async def test_key_in_header_is_enough_and_checked(client):
    """Скрипт таблицы шлёт ключ заголовком X-Flags-Key: в журнал запросов он не попадает."""
    p = _patches()
    with p[0], p[1], p[2], p[3]:
        ok = await client.get(URL, headers={**HEADERS, "X-Flags-Key": KEY})
        bad = await client.get(URL, headers={**HEADERS, "X-Flags-Key": "wrong"})
    assert ok.status_code == 200 and ok.json()["in_bot"] == ["111", "333"]
    assert bad.status_code == 403
