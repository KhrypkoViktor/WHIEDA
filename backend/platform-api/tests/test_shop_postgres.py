"""Мастерская WWC on a real database (V22): the migration twice, the seed catalog,
order → receipt → «Оплачено» → access for a file and a course, several orders in
one ticket, the signed file link, statistics-only partner code, RLS role."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

V22 = "platform_shop_v22.sql"
OWNER = 688931415
BUYER = 70007
OLGA = 52525
STRANGER = 80008
FILE_ID = "6a1b2c3d-0000-4000-8000-000000000001"
IMAGE_ID = "6a1b2c3d-0000-4000-8000-000000000002"
SECRET = "s" * 40

SEED = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('telegram:whieda:{BUYER}', 'whieda', 'Инна', {BUYER}, '{BUYER}'),
  ('proof-olga', 'whieda', 'Ольга', {OLGA}, '{OLGA}');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values ('olga', 'whieda', 'proof-olga', 'named');
insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
values ('whieda', 'telegram:whieda:{BUYER}', 'proof-olga', null, 'admin_manual');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'vozrazheniya', 'Мастерство работы с возражениями', 'purchase', 'published');
insert into academy_media (tenant_id, media_id, owner_telegram_user_id, kind, original_name, mime, size_bytes, storage_key, status)
values ('whieda', '{FILE_ID}', {OWNER}, 'file', 'Objection_Mastery_WWC.pdf', 'application/pdf', 1234,
        'whieda/academy/{FILE_ID}/original.pdf', 'ready'),
       ('whieda', '{IMAGE_ID}', {OWNER}, 'image', 'cover.png', 'image/png', 10,
        'whieda/academy/{IMAGE_ID}/original.png', 'ready');
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "")
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.delenv("PLATFORM_ACADEMY_MEDIA_VIA_API", raising=False)


@pytest.mark.integration
def test_v22_twice_seeds_the_catalog_and_the_pilot_is_for_the_owner_only(monkeypatch):
    _env(monkeypatch)
    assert V22 in MIGRATIONS
    with temporary_database("whieda_shop_seed") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)  # V22 дважды: idempotent
            rows = conn.execute("select code, kind, status, partner_share_wusd_minor, confirmer from shop_items order by sort_order").fetchall()
            db.grant_api_role(conn)
        assert [r[0] for r in rows] == [
            "kurs-online-start", "konsultaciya", "snyat-blok", "lending", "kurs-vozrazheniya", "kurs-prodazhi",
            "preza-vozrazheniya", "gemini",
        ]
        published = {"gemini", "kurs-online-start"}  # «Онлайн-старт» продаётся всем с 06.10.2026
        assert {r[0] for r in rows if r[2] == "published"} == published
        assert {r[2] for r in rows if r[0] not in published} == {"pilot"}
        assert {r[3] for r in rows} == {0}  # доли партнёру в v1 нет
        assert {r[0]: r[4] for r in rows}["gemini"] == "services_admin" and {r[4] for r in rows if r[0] != "gemini"} == {"owner"}

        async def proof() -> None:
            from app.shop.service import public_catalog

            guest = await public_catalog("whieda", viewer_id=None)
            assert [i["code"] for i in guest["items"]] == ["kurs-online-start", "gemini"] and guest["preview"] is False
            online = guest["items"][0]
            assert online["prices"] == {"wusd": 100, "rub": 10000, "byn": 350, "club_wusd": 75, "club_rub": 7500}
            assert online["cta"]["start"] == "shop_kurs-online-start"
            gemini = guest["items"][1]
            assert gemini["cta"] == {"kind": "bot", "start": "gemini", "url": "https://t.me/WHIEDA_Advisor_bot?start=gemini"}
            assert gemini["price_text"] == "6 мес — 3 990 ₽ · 18 мес — 4 490 ₽"
            assert not any("share" in key for key in gemini)
            owner = await public_catalog("whieda", viewer_id=OWNER)
            assert owner["preview"] is True and len(owner["items"]) == 8
            preza = next(i for i in owner["items"] if i["code"] == "preza-vozrazheniya")
            assert preza["prices"] == {"wusd": 5, "rub": 500, "byn": 17.5}
            assert preza["cta"]["start"] == "shop_preza-vozrazheniya" and preza["description_html"].startswith("<p>")
            prodazhi = next(i for i in owner["items"] if i["code"] == "kurs-prodazhi")
            assert prodazhi["available"] is False  # курса «Продажи» ещё нет
            stranger = await public_catalog("whieda", viewer_id=STRANGER)
            assert [i["code"] for i in stranger["items"]] == ["kurs-online-start", "gemini"]

        db.run_with_app(proof)


@pytest.mark.integration
def test_order_receipt_paid_gives_the_file_and_the_course_once(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_shop_orders") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.shop.service import (
                ShopError, confirm_order, file_link, get_item, list_purchases, open_order, reject_order,
                release_receipt, resolve_partner_ref, take_receipt, update_item, waiting_order_at,
            )
            from app.support.service import list_open_tickets_for_admin, open_or_reuse_ticket

            # 1. The owner attaches the PDF (only a ready «file» of the Academy fits).
            with pytest.raises(ShopError) as wrong:
                await update_item("whieda", "preza-vozrazheniya", {"file_media_id": IMAGE_ID}, updated_by=OWNER)
            assert wrong.value.code == "media_wrong_kind"
            with pytest.raises(ShopError) as missing:
                await update_item("whieda", "kurs-prodazhi", {"course_slug": "prodazhi"}, updated_by=OWNER)
            assert missing.value.code == "course_not_found"
            preza = await update_item("whieda", "preza-vozrazheniya", {"file_media_id": FILE_ID, "cover_media_id": IMAGE_ID}, updated_by=OWNER)
            assert preza["file_media_id"] == FILE_ID
            course = await get_item("whieda", "kurs-vozrazheniya")

            # 2. Who brought the buyer: the link wins, a self-link does not count, else the first touch.
            assert await resolve_partner_ref("whieda", telegram_user_id=BUYER, link_ref="OLGA") == ("olga", "link")
            assert await resolve_partner_ref("whieda", telegram_user_id=BUYER, link_ref="nobody") == ("olga", "attribution")
            assert await resolve_partner_ref("whieda", telegram_user_id=OLGA, link_ref="olga") == (None, None)

            # 3. One ticket (channel shop, the owner's «site» forum), two orders in it.
            ticket = await open_or_reuse_ticket(
                "whieda", channel_code="shop", offer_code="preza-vozrazheniya", offer_title=preza["title"],
                user_telegram_user_id=BUYER, user_chat_id=BUYER, user_display="Инна", admin_telegram_user_id=OWNER,
            )
            in_site_forum = await list_open_tickets_for_admin("whieda", admin_telegram_user_id=OWNER, forum_kind="site")
            assert [t["ticket_id"] for t in in_site_forum] == [ticket["ticket_id"]]
            assert await list_open_tickets_for_admin("whieda", admin_telegram_user_id=OWNER, forum_kind="services") == []
            file_order = await open_order(
                "whieda", item=preza, telegram_user_id=BUYER, ticket_id=str(ticket["ticket_id"]), country_code="RU",
                partner_ref_code="olga", partner_ref_source="link",
            )
            course_order = await open_order(
                "whieda", item=course, telegram_user_id=BUYER, ticket_id=str(ticket["ticket_id"]), country_code="BY",
                partner_ref_code="olga", partner_ref_source="attribution",
            )
            assert file_order["created"] and course_order["created"]
            assert (file_order["currency"], file_order["amount_minor"]) == ("RUB", 50000)
            assert (course_order["currency"], course_order["amount_minor"]) == ("WUSD", 2500)
            # «Купить» ещё раз до оплаты — тот же заказ; страну можно сменить до чека.
            again = await open_order(
                "whieda", item=preza, telegram_user_id=BUYER, ticket_id=str(ticket["ticket_id"]), country_code="BY",
                partner_ref_code=None, partner_ref_source=None,
            )
            assert again["created"] is False and again["order_id"] == file_order["order_id"]
            assert (again["currency"], again["amount_minor"], again["partner_ref_code"]) == ("WUSD", 500, "olga")

            # 4. A receipt moves both waiting orders to «receipt»; an old order does not take photos.
            assert await waiting_order_at("whieda", telegram_user_id=BUYER) is not None
            assert await waiting_order_at("whieda", telegram_user_id=STRANGER) is None
            assert await take_receipt("whieda", telegram_user_id=BUYER, file_id="f", now=datetime.now(timezone.utc) + timedelta(days=4)) == []
            taken = await take_receipt("whieda", telegram_user_id=BUYER, file_id="receipt-1")
            assert sorted(o["order_id"] for o in taken) == sorted([file_order["order_id"], course_order["order_id"]])
            assert {o["status"] for o in taken} == {"receipt"} and await take_receipt("whieda", telegram_user_id=BUYER, file_id="x") == []
            assert await waiting_order_at("whieda", telegram_user_id=BUYER) is None
            # The buttons never reached the owner: the order waits for the receipt again.
            assert await release_receipt("whieda", order_id=file_order["order_id"]) is True
            assert await release_receipt("whieda", order_id=file_order["order_id"]) is False
            retaken = await take_receipt("whieda", telegram_user_id=BUYER, file_id="receipt-2")
            assert [(o["order_id"], o["receipt_file_id"]) for o in retaken] == [(file_order["order_id"], "receipt-2")]

            # 5. «Оплачено»: the file and the course open once; a second press changes nothing.
            paid_file = await confirm_order("whieda", order_id=file_order["order_id"], paid_by=OWNER)
            assert paid_file["delivered"] is True and paid_file["order"]["status"] == "delivered" and not paid_file["idempotent"]
            paid_course = await confirm_order("whieda", order_id=course_order["order_id"], paid_by=OWNER)
            assert paid_course["delivered"] is True
            twice = await confirm_order("whieda", order_id=course_order["order_id"], paid_by=OWNER)
            assert twice["idempotent"] is True and twice["order"]["status"] == "delivered"
            async with tenant_connection("whieda") as conn:
                academy = await fetch_all(
                    conn, "select telegram_user_id, source, payment_ref from academy_access where tenant_id = 'whieda'"
                )
                access = await fetch_all(conn, "select item_code, telegram_user_id from shop_access order by item_code")
                ledger = await fetch_all(conn, "select 1 from partner_bonus_ledger")
            assert academy == [{"telegram_user_id": BUYER, "source": "purchase", "payment_ref": f"shop:{course_order['order_id']}"}]
            assert access == [
                {"item_code": "kurs-vozrazheniya", "telegram_user_id": BUYER},
                {"item_code": "preza-vozrazheniya", "telegram_user_id": BUYER},
            ]
            assert ledger == []  # без начислений партнёру (владелец, 03.10.2026)

            # 6. The cabinet: orders and access with a signed link bound to the buyer.
            purchases = await list_purchases("whieda", BUYER)
            assert [o["status"] for o in purchases["orders"]] == ["delivered", "delivered"]
            assert {o["ticket"] for o in purchases["orders"]} == {f"#S-{int(ticket['ticket_no'])}"}
            by_code = {a["item"]["code"]: a for a in purchases["access"]}
            pdf = by_code["preza-vozrazheniya"]
            assert pdf["download_url"].startswith(f"/academy-media/whieda/academy/{FILE_ID}/original.pdf?u={BUYER}&e=")
            assert pdf["file"] == {"name": "Objection_Mastery_WWC.pdf", "size": 1234, "mime": "application/pdf"}
            assert pdf["expires_in"] == 3600 and pdf["item"]["cover_url"].startswith("/academy-media/")
            assert by_code["kurs-vozrazheniya"]["course_url"] == "/academy/?course=vozrazheniya"
            assert by_code["kurs-vozrazheniya"]["download_url"] is None
            link = await file_link("whieda", "preza-vozrazheniya", BUYER)
            assert f"u={BUYER}" in link["url"] and link["name"] == "Objection_Mastery_WWC.pdf"
            with pytest.raises(ShopError) as foreign:
                await file_link("whieda", "preza-vozrazheniya", STRANGER)
            assert foreign.value.code == "purchase_required"
            assert (await file_link("whieda", "preza-vozrazheniya", OWNER))["url"]  # владелец проверяет файл

            # 7. A service: «Отклонить» cancels; after it a new order is possible; «Оплачено» stays «paid».
            service = await get_item("whieda", "snyat-blok")
            first = await open_order(
                "whieda", item=service, telegram_user_id=BUYER, ticket_id=str(ticket["ticket_id"]), country_code="RU",
                partner_ref_code=None, partner_ref_source=None,
            )
            rejected = await reject_order("whieda", order_id=first["order_id"])
            assert rejected["order"]["status"] == "cancelled" and not rejected["idempotent"]
            assert (await confirm_order("whieda", order_id=first["order_id"], paid_by=OWNER))["idempotent"] is True
            second = await open_order(
                "whieda", item=service, telegram_user_id=BUYER, ticket_id=str(ticket["ticket_id"]), country_code="RU",
                partner_ref_code=None, partner_ref_source=None,
            )
            assert second["created"] is True and second["order_id"] != first["order_id"]
            paid_service = await confirm_order("whieda", order_id=second["order_id"], paid_by=OWNER)
            assert paid_service["delivered"] is False and paid_service["order"]["status"] == "paid"

        db.run_with_app(proof)


CLUB = 90001
NO_CLUB = 90002
EXPIRED = 90003
CLUB_SEED = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('telegram:whieda:{CLUB}', 'whieda', 'В клубе', {CLUB}, '{CLUB}'),
  ('telegram:whieda:{NO_CLUB}', 'whieda', 'Без клуба', {NO_CLUB}, '{NO_CLUB}'),
  ('telegram:whieda:{EXPIRED}', 'whieda', 'Клуб истёк', {EXPIRED}, '{EXPIRED}');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
  ('clubber', 'whieda', 'telegram:whieda:{CLUB}', 'named'),
  ('nocl', 'whieda', 'telegram:whieda:{NO_CLUB}', 'named'),
  ('oldclub', 'whieda', 'telegram:whieda:{EXPIRED}', 'named');
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
  ('whieda', 'clubber', now() + interval '60 days'),
  ('whieda', 'nocl', now() + interval '60 days'),
  ('whieda', 'oldclub', now() + interval '60 days');
insert into partner_product_access (tenant_id, ref_code, product_code, paid_until) values
  ('whieda', 'clubber', 'club_subscription', now() + interval '30 days'),
  ('whieda', 'oldclub', 'club_subscription', now() - interval '1 day');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'online-start-4w', 'Онлайн-старт: 4 недели практики', 'purchase', 'published');
"""


@pytest.mark.integration
def test_online_start_is_sold_to_anyone_and_a_club_member_pays_the_club_price(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_shop_club") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS)
            conn.execute(SEED)
            conn.execute(CLUB_SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy.service import AcademyViewer, course_outline, list_courses
            from app.db import tenant_connection
            from app.shop.service import course_offers, get_item, is_club_member, open_order

            # Клуб: действующий club_subscription у профиля этого человека; истёкший и чужой — нет.
            assert await is_club_member("whieda", CLUB) is True
            assert await is_club_member("whieda", NO_CLUB) is False
            assert await is_club_member("whieda", EXPIRED) is False
            assert await is_club_member("whieda", 123456) is False  # человек без профиля
            assert await is_club_member("whieda", BUYER) is False

            # Карточка Мастерской есть только у опубликованного курса (возражения — pilot).
            async with tenant_connection("whieda") as conn:
                offers = await course_offers(conn, "whieda", ["online-start-4w", "vozrazheniya"])
            assert list(offers) == ["online-start-4w"]
            assert offers["online-start-4w"]["start"] == "shop_kurs-online-start"
            assert offers["online-start-4w"]["url"] == "https://t.me/WHIEDA_Advisor_bot?start=shop_kurs-online-start"
            assert offers["online-start-4w"]["prices"]["club_wusd"] == 75

            # Академия для сайта: у закрытого платного курса — purchase, у курса без карточки — нет.
            stranger = AcademyViewer(telegram_user_id=123456, is_preview_admin=False, partner_paid=False)
            listed = {c["slug"]: c for c in await list_courses("whieda", stranger)}
            assert listed["online-start-4w"]["lock_reason"] == "purchase_required"
            assert listed["online-start-4w"]["purchase"]["code"] == "kurs-online-start"
            assert "purchase" not in listed["vozrazheniya"]  # ключ у автора, как раньше
            outline = await course_outline("whieda", "online-start-4w", stranger, allow_locked=True)
            assert outline["course"]["purchase"]["start"] == "shop_kurs-online-start"

            # Заказ: человеку без сайта — обычная цена, участнику клуба — клубная.
            item = await get_item("whieda", "kurs-online-start")
            plain = await open_order("whieda", item=item, telegram_user_id=123456, ticket_id=None, country_code="RU",
                                     partner_ref_code=None, partner_ref_source=None)
            member = await open_order("whieda", item=item, telegram_user_id=CLUB, ticket_id=None, country_code="RU",
                                      partner_ref_code=None, partner_ref_source=None, club=True)
            member_by = await open_order("whieda", item=item, telegram_user_id=CLUB, ticket_id=None, country_code="BY",
                                         partner_ref_code=None, partner_ref_source=None, club=True)
            assert (plain["currency"], plain["amount_minor"]) == ("RUB", 1000000)
            assert (member["currency"], member["amount_minor"]) == ("RUB", 750000)
            assert member_by["order_id"] == member["order_id"]  # тот же заказ, страну сменили до чека
            assert (member_by["currency"], member_by["amount_minor"]) == ("WUSD", 7500)

        db.run_with_app(proof)
