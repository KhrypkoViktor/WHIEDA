"""«Мои заявки» в боте Max на настоящем PostgreSQL (V11 каналы + V14/V20 CRM, роль без bypassrls).

Список — только свои карточки с сайта партнёра, найденного по Max user_id через
account_identities (17.10.2026: строка lead_actor_channels без подтверждения личностью не считается); даты — по часовому поясу его аккаунта;
повторная заявка поднимает карточку; чужие, удалённые и ручные карточки не попадают;
чтение не создаёт CRM-аккаунт. Max (send_max_text) подделан — реальные сообщения не уходят.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED


@pytest.mark.integration
def test_max_my_leads_lists_only_own_site_cards_newest_first(monkeypatch):
    for name in ("PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_CRM_LEAD_CARDS", "true")

    with temporary_database("whieda_maxleads") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(LEAD_TABLES)
            conn.execute(SEED)
            conn.execute("insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id) "
                         "values ('vera-actor', 'whieda', 'Вера', 7003)")
            # Вера: партнёр, ни разу не открывавший WWC CRM (нет platform_accounts).
            conn.execute("insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) "
                         "values ('vera', 'whieda', 'vera-actor', 'named', '{\"subdomain\": \"vera\"}', true)")
            conn.execute("insert into partner_subscriptions (tenant_id, ref_code, paid_until) "
                         "values ('whieda', 'vera', now() + interval '30 days')")
            # Max привязан (подтверждённо — account_identities) к Игорю, Петру и Вере; 9099 — просто человек из Max;
            # 9004 «прилип» к actor Игоря в lead_actor_channels по нику, подтверждения нет.
            conn.execute("insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id) values "
                         "('whieda', 'igor-actor', 'max', '9001'), ('whieda', 'petr-actor', 'max', '9002'), "
                         "('whieda', 'vera-actor', 'max', '9003'), ('whieda', 'igor-actor', 'max', '9004')")
            conn.execute("insert into account_identities (tenant_id, telegram_user_id, provider, external_id, linked_by) values "
                         "('whieda', 7001, 'max', '9001', 'telegram:maxlink'), ('whieda', 7002, 'max', '9002', 'telegram:maxlink'), "
                         "('whieda', 7003, 'max', '9003', 'telegram:maxlink')")
            db.grant_api_role(conn)

        def admin(statement: str, params: tuple = ()) -> None:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                conn.execute(statement, params)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.leads.service import parse_lead_body, save_lead
            from app.max import my_leads, processor
            from app.max.update_parser import MaxEvent
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True})

            async def accounts() -> int:
                async with tenant_connection("whieda") as conn:
                    return len(await fetch_all(conn, "select account_id from platform_accounts"))

            def lead(key: str, contact: str, name: str, ref: str = "igor") -> object:
                return parse_lead_body(
                    {"name": name, "contact": contact, "product": "Стельки", "comment": "вечером",
                     "idempotency_key": key, "initial_ref": ref, "active_ref": ref},
                    tenant_id="whieda",
                )

            def at(day: int, hour: int) -> datetime:
                return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)

            def set_created(name: str, when: datetime) -> None:
                admin("update crm_contacts set created_at = %s where name = %s", (when, name))
                admin("update crm_activities set created_at = %s where kind = 'created' and contact_id = "
                      "(select contact_id from crm_contacts where name = %s)", (when, name))

            igor = await crm.get_or_create_account("whieda", 7001)
            await crm.get_or_create_account("whieda", 7002)
            await crm.set_timezone("whieda", igor, "Asia/Yekaterinburg")

            await save_lead(lead("l-olga", "+7 999 111-22-33", "Ольга"))
            await save_lead(lead("l-marat", "@marat_tg", "Марат"))
            await save_lead(lead("l-irina", "+7 999 222-33-44", "Ирина"))
            await save_lead(lead("l-gone", "+7 999 777-88-99", "Удалённая"))
            await save_lead(lead("l-petr", "+7 999 333-44-55", "Чужая заявка", ref="petr"))
            await crm.create_contact("whieda", igor, name="Борис", phone="+7 999 555-66-77")  # ручной, не заявка
            async with tenant_connection("whieda") as conn:
                gone = await fetch_all(conn, "select contact_id::text as id from crm_contacts where name = 'Удалённая'")
            await crm.delete_contact("whieda", igor, gone[0]["id"])

            set_created("Ольга", at(10, 12))
            set_created("Марат", at(16, 20))  # 20:00 UTC = 01:00 17 октября в Екатеринбурге
            set_created("Ирина", at(5, 9))
            # Повторная заявка Ольги по тому же телефону: карточка одна, но заявка свежая.
            await save_lead(lead("l-olga-2", "8 999 111 22 33", "Ольга"))
            admin("update crm_activities set created_at = %s where kind = 'lead'", (at(17, 5),))

            # ---- список: свои, не удалённые, только с сайта; новые сверху; дата по поясу аккаунта
            result = await crm.recent_site_leads("whieda", 7001, limit=10)
            assert [i["name"] for i in result["items"]] == ["Ольга", "Марат", "Ирина"]
            assert result["more"] is False
            olga, marat, irina = result["items"]
            assert (olga["phone"], olga["status_title"], str(olga["requested_on"])) == ("+79991112233", "Новый контакт", "2026-10-17")
            assert str(marat["requested_on"]) == "2026-10-17", "the partner's own calendar day, not UTC"
            assert (marat["phone"], marat["note_contact"]) == ("", "@marat_tg"), "a handle lives in the request note"
            assert str(irina["requested_on"]) == "2026-10-05"
            two = await crm.recent_site_leads("whieda", 7001, limit=2)
            assert [i["name"] for i in two["items"]] == ["Ольга", "Марат"] and two["more"] is True

            # ---- чужие данные недоступны; чтение не заводит аккаунт
            assert [i["name"] for i in (await crm.recent_site_leads("whieda", 7002))["items"]] == ["Чужая заявка"]
            before = await accounts()
            assert await crm.recent_site_leads("whieda", 7003) is None
            assert await crm.recent_site_leads("whieda", 424242) is None
            assert await accounts() == before
            assert await crm.recent_site_leads("other", 7001) is None, "tenant isolation"

            # ---- весь путь: сообщение Max → привязанный Telegram-аккаунт → ответ бота
            def event(user_id: int, text: str = "Мои заявки") -> MaxEvent:
                return MaxEvent(kind="message", user_id=user_id, chat_id=user_id * 10, text=text, username=None,
                                display_name="Тест", raw={})

            async def ask(user_id: int) -> str:
                sent = AsyncMock(return_value={"ok": True})
                with patch.object(processor, "send_max_text", sent):
                    out = await processor.handle_max_message(tenant, event(user_id), "t1")
                sent.assert_awaited_once()
                assert out["route"] == "max_my_leads"
                return sent.await_args.kwargs["text"]

            assert await ask(9001) == (
                "Заявки с сайта:\n"
                "17.10 · Ольга · +79991112233 · Новый контакт\n"
                "17.10 · Марат · @marat_tg · Новый контакт\n"
                "05.10 · Ирина · +79992223344 · Новый контакт\n"
                "\n"
                "Все заявки и заметки: wwc.best/crm/"
            )
            petr_text = await ask(9002)
            assert "Чужая заявка" in petr_text and "Ольга" not in petr_text and "Марат" not in petr_text
            assert await ask(9003) == my_leads.NO_DIARY_TEXT
            assert await ask(9099) == my_leads.NOT_LINKED_TEXT
            assert await ask(9004) == my_leads.NOT_LINKED_TEXT, "a channel row on the partner's actor is not a login"
            assert await accounts() == before

        db.run_with_app(proof)
