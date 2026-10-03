"""Кабинет партнёра /me/ против настоящего PostgreSQL (platform_cabinet_v21.sql, роль без bypassrls).

1. V21 дважды подряд (вместе со всеми миграциями) — без ошибок;
2. фото: обработка → partner_media → публичная отдача тех же байтов;
3. заявка → вторая заявка заменяет первую и сохраняет её поля → «Применить» в боте
   владельца → public_profile, profile_version + 1, публичный /ref отдаёт bio и
   контакты; повторное нажатие ничего не меняет;
4. отказ с причиной (Reply владельца), отзыв партнёром, одна pending на ref (индекс);
5. «Мои рефералы» и история WWC$ постранично, баланс и правила;
6. overview: уровни free/pro, путь, счётчики Академии и WWC CRM, ручные шаги, настройки;
7. RLS: другой тенант не видит ни заявок, ни фото.

Telegram не вызывается: отправка и подтверждение нажатий — моки.
"""

from __future__ import annotations

import os
import uuid
from io import BytesIO
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from PIL import Image

from tests.postgres_testkit import temporary_database

OLGA = 525317405
FREE = 7003
OWNER = 688931415

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('olga-actor', 'whieda', 'Ольга', '525317405'),
  ('free-actor', 'whieda', '@free', '7003'),
  ('anna-actor', 'whieda', 'Анна', '7101'),
  ('boris-actor', 'whieda', 'Борис', '7102'),
  ('vera-actor', 'whieda', 'Вера', '7103');
update lead_actors set telegram_user_id = 525317405 where actor_id = 'olga-actor';
update lead_actors set telegram_user_id = 7003 where actor_id = 'free-actor';
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled, profile_version) values
  ('olga-samtsova', 'whieda', 'olga-actor', 'named',
   '{"display_name": "Ольга Самцова", "subdomain": "samtsova", "vk_url": "https://vk.com/old",
     "selected_theme_id": "sankofa", "photo_url": "/media/partners/olga-samtsova.webp"}', true, 4),
  ('anna', 'whieda', 'anna-actor', 'named', '{}', true, 1);
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
  ('whieda', 'olga-samtsova', now() + interval '30 days'),
  ('whieda', 'anna', now() + interval '10 days');
insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, source, created_at) values
  ('whieda', 'anna-actor', 'olga-actor', 'admin_manual', now() - interval '3 days'),
  ('whieda', 'boris-actor', 'olga-actor', 'admin_manual', now() - interval '2 days'),
  ('whieda', 'vera-actor', 'olga-actor', 'admin_manual', now() - interval '1 day');
insert into partner_bonus_ledger (
  tenant_id, actor_id, entry_type, amount_minor, currency, product_code, idempotency_key, description, created_at
) values
  ('whieda', 'olga-actor', 'credit', 600, 'WUSD', 'platform_subscription', 'k1', 'Referral bonus: first payment', now() - interval '3 days'),
  ('whieda', 'olga-actor', 'credit', 300, 'WUSD', 'platform_subscription', 'k2', 'Referral bonus: renewal', now() - interval '2 days'),
  ('whieda', 'olga-actor', 'admin_adjustment', 150, 'WUSD', 'platform_subscription', 'k3', 'переплата', now() - interval '1 day');
insert into academy_courses (tenant_id, slug, title, access_rule, status) values
  ('whieda', 'zapusk-wwc', 'Запуск WWC', 'pro', 'published');
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html, status)
select 'whieda', course_id, lesson.slug, lesson.position, lesson.title, '<p>.</p>', 'published'
from academy_courses, (values ('start', 1, 'Старт'), ('site', 2, 'Сайт'), ('crm', 3, 'CRM')) as lesson(slug, position, title)
where academy_courses.slug = 'zapusk-wwc';
insert into academy_progress (tenant_id, telegram_user_id, lesson_id)
select 'whieda', 525317405, lesson_id from academy_lessons where slug = 'start';
insert into tenants (tenant_id, display_name, status) values ('other', 'Other', 'active')
  on conflict (tenant_id) do nothing;
"""


def _jpeg(size=(2400, 1800)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, (90, 120, 200)).save(out, format="JPEG", quality=90)
    return out.getvalue()


def _binding():
    from app.telegram.bindings import BotBindingContext
    from app.tenancy import TenantContext

    return BotBindingContext(
        binding_id="whieda-advisor-bot",
        tenant=TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True}),
        bot_token_ref="env:TEST_TOKEN",
        webhook_secret_ref="env:TEST_SECRET",
        bot_username="test_bot",
        status="active",
        processing_mode="core",
        bot_token="test-token",
        webhook_secret="test-secret",
    )


def _callback(data: str):
    from app.telegram.update_parser import parse_telegram_callback

    return parse_telegram_callback(
        {"callback_query": {"id": "cb", "data": data, "from": {"id": OWNER},
                            "message": {"message_id": 70, "chat": {"id": OWNER, "type": "private"}}}}
    )


def _reply(text: str, prompt: str):
    from app.telegram.update_parser import parse_telegram_message

    return parse_telegram_message(
        {"message": {"message_id": 71, "text": text, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER},
                     "reply_to_message": {"message_id": 901, "from": {"id": 1, "is_bot": True}, "text": prompt}}}
    )


@pytest.mark.integration
def test_cabinet_profile_request_apply_photo_pages_and_overview(monkeypatch):
    for name in ("PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_LEADER_PILOT_TELEGRAM_IDS",
                 "PLATFORM_PARTNER_MEDIA_PUBLIC_BASE", "PLATFORM_TENANT_MEDIA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_LEADER_PILOT_TELEGRAM_IDS", "")
    monkeypatch.setenv("PLATFORM_TENANT_MEDIA_BASE_URL", "https://media.sysarch.pro")

    with temporary_database("whieda_cabinet") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)  # V21 идемпотентна вместе со всеми
            conn.execute(SEED)
            db.grant_api_role(conn)
            indexes = {
                row[0]
                for row in conn.execute(
                    "select indexname from pg_indexes where tablename in ('partner_profile_requests', 'partner_media')"
                ).fetchall()
            }
            assert {"partner_profile_requests_one_pending", "partner_media_ref_created"} <= indexes

        def admin(statement: str, params: tuple = ()) -> list[tuple]:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                cursor = conn.execute(statement, params)
                return cursor.fetchall() if cursor.description else []

        async def proof() -> None:
            from app.cabinet import service as cabinet
            from app.cabinet.photos import process_profile_photo
            from app.cabinet.profile import ProfileValidationError
            from app.ref.service import format_public_ref, load_public_ref
            from app.referral_bonus.service import list_bonus_ledger, list_referrals
            from app.telegram import cabinet_profile as moderation
            from app.telegram.bindings import binding_context_scope
            from app.telegram.consent import marketing_consent_state
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True})
            olga = await cabinet.load_person("whieda", OLGA)
            assert olga.ref_code == "olga-samtsova" and olga.partner_paid and olga.actor_id == "olga-actor"

            # ---- 2. фото -----------------------------------------------------------------
            photo = process_profile_photo(_jpeg())
            stored = await cabinet.store_profile_photo("whieda", olga, photo)
            assert stored["photo_url"] == f"https://wwc.best/api/v1/content-access/partner-media/{stored['media_id']}.jpg"
            assert (stored["width"], stored["height"]) == (1200, 900)
            assert await cabinet.check_upload_allowed("whieda", olga) == "olga-samtsova"
            assert stored["preview_url"] == f"/api/v1/content-access/me/profile/photo/{stored['media_id']}.jpg"
            # До «Применить» фото видит только сам партнёр: публичный адрес молчит.
            assert await cabinet.load_public_media("whieda", stored["media_id"]) is None
            assert await cabinet.load_own_media("whieda", olga, stored["media_id"]) == photo.body
            free_person = await cabinet.load_person("whieda", FREE)
            assert await cabinet.load_own_media("whieda", free_person, stored["media_id"]) is None
            assert await cabinet.load_own_media("other", olga, stored["media_id"]) is None  # RLS
            assert await cabinet.load_public_media("whieda", "not-a-uuid") is None

            # ---- 3. заявка, замена, «Применить» ------------------------------------------------
            first = await cabinet.submit_profile_request(
                "whieda", olga, {"photo_url": stored["photo_url"], "contacts": {"phone": "8 916 123-45-67"}}
            )
            assert first["status"] == "pending" and first["replaced"] is False
            second = await cabinet.submit_profile_request(
                "whieda",
                olga,
                {
                    "display_name": "Ольга С.",
                    "bio": "Помогаю разобраться в продуктах.\n\nПишите.",
                    "socials": {"vk_url": None, "telegram_channel_url": "https://t.me/olga"},
                },
            )
            assert second["replaced"] is True
            changes = second["pending"]["changes"]
            assert changes == {
                "display_name": "Ольга С.",
                "bio": "Помогаю разобраться в продуктах.\n\nПишите.",
                "photo_url": stored["photo_url"],
                "contacts": {"phone": "+79161234567"},
                "socials": {"vk_url": None, "telegram_channel_url": "https://t.me/olga"},
            }
            assert second["pending"]["previous"]["socials"]["vk_url"] == "https://vk.com/old"
            assert cabinet.request_out(second["pending"])["photo_preview_url"] == stored["preview_url"]
            assert admin("select status from partner_profile_requests order by created_at") == [("replaced",), ("pending",)]

            foreign = f"https://wwc.best/api/v1/content-access/partner-media/{uuid.uuid4()}.jpg"
            with pytest.raises(ProfileValidationError) as bad_photo:
                await cabinet.submit_profile_request("whieda", olga, {"photo_url": foreign})
            assert bad_photo.value.code == "invalid_photo"
            assert admin("select count(*) from partner_profile_requests where status = 'pending'") == [(1,)]

            token = second["pending"]["request_id"].replace("-", "")
            sent: list[tuple] = []

            async def deliver(chat_id, text, *, reply_markup=None):
                sent.append((chat_id, text, reply_markup))
                return {"ok": True, "message_id": 901}

            with binding_context_scope(_binding()), patch.object(moderation, "answer_callback_query", AsyncMock()), \
                    patch.object(moderation, "_call_telegram", AsyncMock(return_value={"ok": True})), \
                    patch.object(moderation, "_deliver", deliver):
                applied = await moderation.try_handle_profile_callback(tenant, _callback(f"prof:apply:{token}"), trace_id="t")
                again = await moderation.try_handle_profile_callback(tenant, _callback(f"prof:apply:{token}"), trace_id="t")
            assert applied["status"] == "applied" and again["status"] == "applied"
            partner_note = next(item for item in sent if item[0] == OLGA)
            assert partner_note[1].startswith("Изменения на сайте применены: имя, «о себе», фото, телефон")
            login_url = partner_note[2]["inline_keyboard"][0][0]["url"]
            # Вход по ссылке живёт в таблицах content-access (их нет в testkit) — ссылка без него не ломается.
            assert login_url.split("#", 1)[0] == "https://samtsova.wwc.best/me/"
            assert sent[-1] == (OWNER, "Заявка уже применена.", None)

            profile_row = admin(
                "select public_profile, profile_version from referral_profiles where ref_code = 'olga-samtsova'"
            )[0]
            public_profile, version = profile_row
            assert version == 5
            assert public_profile["display_name"] == "Ольга С." and public_profile["photo_url"] == stored["photo_url"]
            assert public_profile["contacts"] == {"phone": "+79161234567"}
            assert public_profile["socials"] == {"telegram_channel_url": "https://t.me/olga"}
            assert "vk_url" not in public_profile
            assert public_profile["subdomain"] == "samtsova" and public_profile["selected_theme_id"] == "sankofa"

            assert await cabinet.load_public_media("whieda", stored["media_id"]) == photo.body  # теперь на сайте
            assert await cabinet.load_public_media("other", stored["media_id"]) is None  # RLS
            public = format_public_ref(await load_public_ref("whieda", "olga-samtsova"))
            consultant = public["consultant"]
            assert public["profile_version"] == 5
            assert consultant["display_name"] == "Ольга С."
            assert consultant["bio"] == "Помогаю разобраться в продуктах.\n\nПишите."
            assert consultant["photoUrl"] == stored["photo_url"]  # абсолютный адрес — без media-хоста
            assert consultant["socials"]["phone"] == "+79161234567"
            assert consultant["socials"]["telegramChannelUrl"] == "https://t.me/olga"
            assert consultant["socials"]["vkUrl"] is None

            # ---- 4. отказ с причиной, отзыв, одна pending ------------------------------------------
            olga = await cabinet.load_person("whieda", OLGA)
            third = await cabinet.submit_profile_request("whieda", olga, {"bio": "Опечатка в тексте"})
            third_id = third["pending"]["request_id"]
            sent.clear()
            with binding_context_scope(_binding()), patch.object(moderation, "answer_callback_query", AsyncMock()), \
                    patch.object(moderation, "_deliver", deliver):
                asked = await moderation.try_handle_profile_callback(
                    tenant, _callback(f"prof:reject:{third_id.replace('-', '')}"), trace_id="t"
                )
                prompt = sent[-1][1]
                rejected = await moderation.try_handle_profile_reject_reason(tenant, _reply("Есть опечатка", prompt), trace_id="t")
            assert asked["status"] == "reason_asked" and rejected["status"] == "rejected"
            assert admin(
                "select status, reject_reason, reviewed_by, reason_prompt_message_id from partner_profile_requests where request_id = %s",
                (third_id,),
            ) == [("rejected", "Есть опечатка", OWNER, 901)]
            assert any(item[0] == OLGA and item[1].startswith("Отклонено: Есть опечатка.") for item in sent)
            pending = await cabinet.get_pending_request("whieda", olga)
            assert pending["pending"] is None and pending["last_review"]["reject_reason"] == "Есть опечатка"

            fourth = await cabinet.submit_profile_request("whieda", olga, {"contacts": {"email": "olga@mail.ru"}})
            assert fourth["status"] == "pending"
            assert await cabinet.cancel_pending_request("whieda", olga) == fourth["pending"]["request_id"]
            assert await cabinet.cancel_pending_request("whieda", olga) is None
            with pytest.raises(cabinet.CabinetError) as nothing:
                await cabinet.submit_profile_request("whieda", olga, {"display_name": "Ольга С."})
            assert nothing.value.code == "no_changes"
            # Вернул всё как на сайте — ожидающая заявка отзывается.
            viber = await cabinet.submit_profile_request("whieda", olga, {"contacts": {"viber": "+375291112233"}})
            back = await cabinet.submit_profile_request("whieda", olga, {"contacts": {"viber": None}})
            assert back == {"pending": None, "status": "no_changes", "replaced_request_id": viber["pending"]["request_id"]}
            with pytest.raises(psycopg.errors.UniqueViolation):
                admin(
                    "insert into partner_profile_requests (tenant_id, ref_code, telegram_user_id) values "
                    "('whieda', 'olga-samtsova', 1), ('whieda', 'olga-samtsova', 2)"
                )
            # Ничейное фото старше суток уходит при следующей заявке, фото сайта остаётся.
            admin(
                "insert into partner_media (tenant_id, ref_code, telegram_user_id, content_type, body, width, height, sha256, created_at)"
                " values ('whieda', 'olga-samtsova', %s, 'image/jpeg', '\\xffd8'::bytea, 10, 10, repeat('a', 64), now() - interval '2 days')",
                (OLGA,),
            )
            admin("update partner_media set created_at = now() - interval '2 days'")
            await cabinet.submit_profile_request("whieda", olga, {"contacts": {"address": "Москва, Садовническая, 58"}})
            assert admin("select media_id::text from partner_media") == [(stored["media_id"],)]

            # ---- 5. рефералы и история ----------------------------------------------------------
            page = await list_referrals("whieda", "olga-actor", limit=2)
            assert [item["display_name"] for item in page["items"]] == ["Вера", "Борис"] and page["next_cursor"]
            rest = await list_referrals("whieda", "olga-actor", limit=2, cursor=page["next_cursor"])
            assert [item["display_name"] for item in rest["items"]] == ["Анна"] and rest["next_cursor"] is None
            assert rest["items"][0]["subscription_status"] == "active"
            ledger = await list_bonus_ledger("whieda", "olga-actor", limit=2)
            assert [item["amount_minor"] for item in ledger["items"]] == [150, 300]
            older = await list_bonus_ledger("whieda", "olga-actor", limit=2, cursor=ledger["next_cursor"])
            assert [item["amount_minor"] for item in older["items"]] == [600] and older["next_cursor"] is None
            summary = await cabinet.bonus_balance_and_rules("whieda", "olga-actor")
            assert summary["balance_minor"] == 1050
            assert summary["rules"]["first_payment_percent"] == 20 and summary["rules"]["renewal_percent"] == 10

            # ---- 6. overview, путь, настройки ----------------------------------------------------
            marked = await cabinet.mark_journey_step("whieda", olga, "presentation")
            again_marked = await cabinet.mark_journey_step("whieda", olga, "presentation")
            assert marked["done_at"] == again_marked["done_at"]
            overview = await cabinet.load_overview("whieda", olga, bot_username="test_bot", crm_entitled=True)
            assert overview["tier"] == "pro" and overview["locks"] == {"club": "club_required", "team": "leader_required"}
            steps = {step["key"]: step["status"] for step in overview["journey"]["steps"]}
            assert steps["presentation"] == "done" and steps["lesson1"] == "done" and steps["invite_sent"] == "done"
            assert steps["profile"] == "done"  # фото, «о себе» и телефон уже на сайте
            assert steps["crm_contact"] == "current" and steps["club"] == "locked"
            assert overview["counters"]["academy"]["lessons_done"] == 1 and overview["counters"]["academy"]["lessons_total"] == 3
            assert overview["counters"]["invited"] == 3 and overview["counters"]["paid"] == 1
            assert overview["counters"]["crm_today"] == 0 and overview["counters"]["crm_contacts"] == 0
            assert overview["invite"]["link"].startswith("https://t.me/test_bot?start=ref_")
            assert overview["profile"]["pending"]["changes"] == {"contacts": {"address": "Москва, Садовническая, 58"}}
            assert overview["site"]["host"] == "samtsova.wwc.best" and overview["site"]["status"] == "active"

            settings = await cabinet.update_settings("whieda", olga, timezone_name="Asia/Yekaterinburg", marketing_opt_in=True)
            assert settings["timezone"] == "Asia/Yekaterinburg" and settings["marketing_opt_in"] is True
            assert await marketing_consent_state("whieda", OLGA) is True
            with pytest.raises(cabinet.CabinetError) as tz:
                await cabinet.update_settings("whieda", olga, timezone_name="Mars/Base")
            assert tz.value.code == "invalid_timezone"

            free = await cabinet.load_person("whieda", FREE)
            free_view = await cabinet.load_overview("whieda", free, bot_username="test_bot", crm_entitled=True)
            assert free_view["tier"] == "free" and free_view["site"] is None and free_view["profile"] is None
            assert free_view["locks"]["crm"] == "pro_required" and free_view["journey"]["path"] == "free"
            with pytest.raises(cabinet.CabinetError) as locked:
                await cabinet.submit_profile_request("whieda", free, {"bio": "Хочу"})
            assert (locked.value.code, locked.value.status) == ("pro_required", 402)

            stranger = await cabinet.load_person("whieda", 999001)  # вошёл на сайт, в базе ещё нет
            assert stranger.actor_id == "telegram:whieda:999001" and stranger.ref_code is None
            assert (await cabinet.load_overview("whieda", stranger, bot_username=None, crm_entitled=True))["tier"] == "free"

            # ---- 7. RLS ----------------------------------------------------------------------------
            from app.db import fetch_all, tenant_connection

            async with tenant_connection("other") as conn:
                assert await fetch_all(conn, "select request_id from partner_profile_requests") == []
                assert await fetch_all(conn, "select media_id from partner_media") == []
                assert await fetch_all(conn, "select step from partner_journey_marks") == []

        db.run_with_app(proof)
