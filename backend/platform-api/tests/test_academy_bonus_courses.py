"""Курс в подарок к покупке (владелец, 12.10.2026: «всем, кто купил курс — открыть бесплатный базовый курс»):
купил «Онлайн-старт» в Мастерской или строкой оплаты — открывается и «Запуск WWC» (source 'gift')."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.academy import service

COURSES = {"online-start-4w": "11111111-1111-1111-1111-111111111111", "zapusk-wwc": "22222222-2222-2222-2222-222222222222"}


class FakeCursor:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params):
        self.log.append((" ".join(query.split()), params))


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


async def fake_fetch_one(conn, query, params=()):
    q = " ".join(query.split())
    if q.startswith("select course_id::text as course_id from academy_courses"):
        slug = params[1]
        return {"course_id": COURSES[slug]} if slug in COURSES else None
    if q.startswith("select la.telegram_user_id from referral_profiles"):
        return {"telegram_user_id": 700}
    raise AssertionError(q[:60])


def _grants(conn):
    """(course_id, источник) каждой записи доступа; источник — литерал в SQL."""
    return [
        (params[1], "gift" if "'gift'" in query else "purchase")
        for query, params in conn.log
        if query.startswith("insert into academy_access")
    ]


@pytest.mark.asyncio
async def test_shop_purchase_of_online_start_also_opens_the_basic_course():
    conn = FakeConn()
    with patch.object(service, "fetch_one", fake_fetch_one):
        assert await service.grant_course_to_telegram_user(conn, "whieda", course_slug="online-start-4w", telegram_user_id=700, payment_ref="order:1")
    assert _grants(conn) == [(COURSES["online-start-4w"], "purchase"), (COURSES["zapusk-wwc"], "gift")]
    gift = conn.log[-1]
    assert gift[1][3] == "bonus:online-start-4w" and "do update set revoked_at = null, expires_at = null" in gift[0]


@pytest.mark.asyncio
async def test_payment_line_path_gives_the_same_gift():
    conn = FakeConn()
    with patch.object(service, "fetch_one", fake_fetch_one):
        assert await service.grant_course_access(conn, "whieda", "zarina", "online-start-4w", "pay:1") == 700
    assert [kind for _, kind in _grants(conn)] == ["purchase", "gift"]


@pytest.mark.asyncio
async def test_other_courses_bring_no_gift_and_a_missing_gift_course_is_skipped():
    conn = FakeConn()
    with patch.object(service, "fetch_one", fake_fetch_one):
        await service.grant_course_to_telegram_user(conn, "whieda", course_slug="zapusk-wwc", telegram_user_id=700, payment_ref="x")
    assert [kind for _, kind in _grants(conn)] == ["purchase"]
    conn = FakeConn()
    with patch.object(service, "fetch_one", fake_fetch_one), patch.dict(service.BONUS_COURSES, {"online-start-4w": ("nope",)}):
        assert await service.grant_bonus_courses(conn, "whieda", course_slug="online-start-4w", telegram_user_id=700) == []
    assert conn.log == []
