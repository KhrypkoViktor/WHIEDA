"""Статус человека в боте: lead_actors.bot_stage (V49, 04.11.2026) в настоящем PostgreSQL (роль API без bypassrls).

1. V49 идемпотентна (дважды подряд), знака доллара нет, зарегистрирована во всех списках миграций и стоит после V48;
2. колонка: text not null default 'guest', ограничение на три значения; заполнение — включённый referral_profiles →
   partner_wwc, остальные guest; повторный прогон не откатывает partner_whieda и не трогает отключённые профили;
3. чтение статуса: гость по умолчанию, выключенный профиль — всё равно partner_wwc, владелец и админ — partner_wwc, профиль сайта — partner_wwc (и поле подтягивается),
   канал Max и привязанная учётка находят того же человека, чужой арендатор статуса не видит;
3a. оплативший без активации и партнёр с выключенным профилем — partner_wwc (бонусы и ссылка не пропадают), а поле
   bot_stage при этом не пишется; анкета, не дошедшая до оплаты, отклонённая и отменённая заявки — гость;
4. повышение: partner_whieda ставится один раз, повтор — already, партнёра WWC вниз не отдаёт; «снять» возвращает
   в гости только partner_whieda; поиск человека по @нику и по id;
5. база без колонки (код выкатили раньше миграции): статус читается по профилю сайта, бот не падает.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_meeting_registrations_postgres import _env

V48 = "platform_pricing_november_v48.sql"
V49 = "platform_bot_stage_v49.sql"
ROOT = Path(__file__).resolve().parents[3]

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id, telegram_username) values
  ('igor-actor', 'whieda', 'Игорь', '7001', 7001, 'igor_wwc'),
  ('olga-actor', 'whieda', 'Ольга', '7002', 7002, null),
  ('off-actor', 'whieda', 'Отключённый', '7003', 7003, null),
  ('telegram:whieda:7100', 'whieda', '@anna_guest', '7100', 7100, null),
  ('telegram:whieda:7200', 'whieda', 'Telegram user 7200', '7200', 7200, null),
  ('max:whieda:9300', 'whieda', 'Max person', null, null, null),
  ('telegram:other:7001', 'other', 'Игорь в чужом тенанте', '7001', 7001, null);
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('igor', 'whieda', 'igor-actor', 'named', '{"subdomain": "igor"}', true),
  ('olga', 'whieda', 'olga-actor', 'named', '{"subdomain": "olga"}', true),
  ('off', 'whieda', 'off-actor', 'named', '{"subdomain": "off"}', false);
"""


def test_v49_has_no_dollar_sign_a_lock_timeout_and_is_registered_everywhere_once_after_v48():
    text = (SQL_DIR / V49).read_text(encoding="utf-8")
    assert "$" not in text  # боевые правки идут из контейнера и через n8n: знак доллара режется
    assert "set local lock_timeout" in text
    for relative in (
        "postgres/scripts/apply_staging_platform_all.ps1",
        "postgres/scripts/staging_proof_lib.py",
        "backend/platform-api/tests/postgres_testkit.py",
    ):
        listed = (ROOT / relative).read_text(encoding="utf-8")
        assert listed.count(V49) == 1, relative
        assert listed.index(V48) < listed.index(V49), relative
    assert MIGRATIONS.index(V49) == MIGRATIONS.index(V48) + 1  # V49 стоит сразу за V48; позже могут идти другие (V50)


@pytest.mark.integration
def test_the_bot_stage_column_the_backfill_and_the_transitions(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", "5000")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "5001")
    from app.settings import get_settings

    get_settings.cache_clear()

    with temporary_database("whieda_bot_stage") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, [name for name in MIGRATIONS if name != V49])  # всё, кроме V49: у людей ещё нет поля
            conn.execute(SEED)
            conn.execute(
                "insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, channel_chat_id, display_name) "
                "values ('whieda', 'max:whieda:9300', 'max', '9300', null, 'Max person'), "
                "('whieda', 'olga-actor', 'max', '9301', null, 'Ольга в Max')"
            )
            assert not conn.execute(
                "select 1 from information_schema.columns where table_name = 'lead_actors' and column_name = 'bot_stage'"
            ).fetchall()

            conn.execute((SQL_DIR / V49).read_text(encoding="utf-8"))
            column = conn.execute(
                "select data_type, is_nullable, column_default from information_schema.columns "
                "where table_name = 'lead_actors' and column_name = 'bot_stage'"
            ).fetchone()
            assert column[0] == "text" and column[1] == "NO" and "'guest'" in column[2]

            def stages() -> dict[str, str]:
                return dict(conn.execute("select actor_id, bot_stage from lead_actors order by actor_id").fetchall())

            # заполнение: включённый профиль — partner_wwc; отключённый профиль и все остальные — guest
            assert stages() == {
                "igor-actor": "partner_wwc", "olga-actor": "partner_wwc", "off-actor": "guest",
                "telegram:whieda:7100": "guest", "telegram:whieda:7200": "guest", "max:whieda:9300": "guest",
                "telegram:other:7001": "guest",
            }
            # ограничение: ровно три значения
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update lead_actors set bot_stage = 'owner' where actor_id = 'igor-actor'")
            with pytest.raises(psycopg.errors.NotNullViolation):
                conn.execute("update lead_actors set bot_stage = null where actor_id = 'igor-actor'")

            # повторный прогон: partner_whieda не откатывается, отключённый профиль гостя не повышает
            conn.execute("update lead_actors set bot_stage = 'partner_whieda' where actor_id = 'telegram:whieda:7200'")
            for _ in range(2):
                conn.execute((SQL_DIR / V49).read_text(encoding="utf-8"))
            assert stages()["telegram:whieda:7200"] == "partner_whieda"
            assert stages()["off-actor"] == "guest" and stages()["igor-actor"] == "partner_wwc"
            conn.execute("update lead_actors set bot_stage = 'guest' where actor_id = 'telegram:whieda:7200'")
            conn.execute(
                "insert into account_identities (tenant_id, telegram_user_id, provider, external_id) values "
                "('whieda', 7002, 'max', '9302')"
            )
            # Оплативший без активации и прочие: профиль сайта и заявка на сайт в разных состояниях
            conn.execute(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id) values "
                "('paid-actor', 'whieda', 'Оплатила', '7300', 7300), ('receipt-actor', 'whieda', 'Чек', '7301', 7301), "
                "('disabled-actor', 'whieda', 'Выключен', '7302', 7302), ('awaiting-actor', 'whieda', 'Анкета', '7303', 7303), "
                "('rejected-actor', 'whieda', 'Отклонена', '7304', 7304), ('cancelled-actor', 'whieda', 'Отменена', '7305', 7305)"
            )
            conn.execute(
                "insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values "
                "('paid', 'whieda', 'paid-actor', 'named', '{\"subdomain\": \"paid\"}', false), "
                "('syncoff', 'whieda', 'disabled-actor', 'named', '{}', false)"
            )
            conn.execute(
                "insert into partner_site_requests (tenant_id, actor_id, status, requested_subdomain) values "
                "('whieda', 'paid-actor', 'pending_provisioning', 'paid'), "          # оплата подтверждена, профиль ещё выключен
                "('whieda', 'receipt-actor', 'pending_confirmation', 'receipt'), "    # чек отправлен, ждёт подтверждения
                "('whieda', 'awaiting-actor', 'awaiting_photo', 'awaiting'), "        # анкета не дошла до оплаты
                "('whieda', 'rejected-actor', 'rejected', 'rejected'), "
                "('whieda', 'cancelled-actor', 'cancelled', 'cancelled')"
            )
            # Академия: курс, купленный человеком 7100; отозванный подарок у 7200; пройденный урок у партнёра 7002
            conn.execute(
                "insert into academy_courses (tenant_id, slug, title, access_rule, status) values ('whieda', 'kurs', 'Курс', 'purchase', 'published')"
            )
            conn.execute(
                "insert into academy_lessons (tenant_id, course_id, slug, module_title, position, title, body_html) "
                "select 'whieda', course_id, 'urok-1', 'Неделя 1', 1, 'Урок 1', '<p>1</p>' from academy_courses where slug = 'kurs'"
            )
            conn.execute(
                "insert into academy_access (tenant_id, course_id, telegram_user_id, source, revoked_at) "
                "select 'whieda', course_id, 7100, 'purchase', null from academy_courses where slug = 'kurs'"
            )
            conn.execute(
                "insert into academy_access (tenant_id, course_id, telegram_user_id, source, revoked_at) "
                "select 'whieda', course_id, 7200, 'gift', now() from academy_courses where slug = 'kurs'"
            )
            conn.execute(
                "insert into academy_progress (tenant_id, telegram_user_id, lesson_id) "
                "select 'whieda', 7002, lesson_id from academy_lessons where slug = 'urok-1'"
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.bot_stages import service
            from app.db import fetch_all, tenant_connection

            async def column_of(actor_id: str) -> str:
                async with tenant_connection("whieda") as c:
                    rows = await fetch_all(c, "select bot_stage from lead_actors where actor_id = %s", (actor_id,))
                return rows[0]["bot_stage"]

            async def stage(channel: str, user: int, tenant_id: str = "whieda") -> str:
                return await service.person_stage(tenant_id, channel, user)

            # --- чтение ---
            assert await stage("telegram", 7100) == "guest"           # обычный человек из бота
            assert await stage("telegram", 99999) == "guest"          # которого бот ещё не знает
            assert await stage("telegram", 7001) == "partner_wwc"     # профиль сайта
            assert await stage("telegram", 7003) == "partner_wwc"     # профиль выключен: кабинет с бонусами и ссылкой остаётся
            assert await stage("telegram", 5000) == "partner_wwc"     # владелец, даже без строки в базе
            assert await stage("telegram", 5001) == "partner_wwc"     # суперадмин
            assert await stage("max", 9300) == "guest"                # Max: человек без профиля
            assert await stage("max", 9301) == "partner_wwc"          # Max: канал привязан к партнёру
            assert await stage("max", 9302) == "partner_wwc"          # Max: привязанная учётка партнёра
            assert await stage("telegram", 7001, "other") == "guest"  # чужой арендатор: у него свои строки и нет профиля
            # поле у партнёра, которое активировали после миграции, подтягивается при первом касании
            async with tenant_connection("whieda") as c:
                await c.execute("update lead_actors set bot_stage = 'guest' where actor_id = 'igor-actor'")
            assert await column_of("igor-actor") == "guest"
            assert await stage("telegram", 7001) == "partner_wwc"
            assert await column_of("igor-actor") == "partner_wwc"

            # --- оплативший без активации и выключенный профиль: partner_wwc, поле не трогаем ---
            assert await stage("telegram", 7300) == "partner_wwc"   # заявка pending_provisioning, профиль enabled=false
            assert await stage("telegram", 7301) == "partner_wwc"   # заявка pending_confirmation, профиля ещё нет
            assert await stage("telegram", 7302) == "partner_wwc"   # профиль enabled=false из синхронизации, заявки нет
            for key in (7303, 7304, 7305):                          # awaiting_*, rejected, cancelled — человек остаётся гостем
                assert await stage("telegram", key) == "guest", key
            for actor in ("paid-actor", "receipt-actor", "disabled-actor"):
                assert await column_of(actor) == "guest", actor     # выключенный профиль и заявка в очередь в поле не оседают
            # заявку отклонили — человек снова гость (поле не писали, держаться нечему)
            async with tenant_connection("whieda") as c:
                await c.execute("update partner_site_requests set status = 'rejected' where actor_id = 'receipt-actor'")
            assert await stage("telegram", 7301) == "guest"
            # поле, уже записанное как partner_whieda, оплата не трогает: статус partner_wwc виден, а в поле остаётся прежнее
            async with tenant_connection("whieda") as c:
                await c.execute("update lead_actors set bot_stage = 'partner_whieda' where actor_id = 'paid-actor'")
            assert await stage("telegram", 7300) == "partner_wwc" and await column_of("paid-actor") == "partner_whieda"

            # --- доступ к Академии (кнопка «🎓 Академия» в меню гостя) ---
            async def academy(channel: str, user: int, tenant_id: str = "whieda") -> bool:
                async with tenant_connection(tenant_id) as c:
                    return await service.has_academy_access(c, tenant_id, channel, user)

            assert await academy("telegram", 7100) is True    # куплен курс
            assert await academy("telegram", 7200) is False   # доступ отозван
            assert await academy("telegram", 7002) is True    # пройден урок
            assert await academy("telegram", 99999) is False  # человека с доступом нет
            assert await academy("max", 9302) is True         # Max-учётка привязана к 7002
            assert await academy("max", 9300) is False        # Max-человек без привязки
            assert await academy("telegram", 7100, "other") is False  # чужой арендатор доступа не видит

            # --- повышение ---
            assert await service.promote("whieda", "telegram", 7100, "partner_whieda") == "changed"
            assert await column_of("telegram:whieda:7100") == "partner_whieda"
            assert await stage("telegram", 7100) == "partner_whieda"
            assert await service.promote("whieda", "telegram", 7100, "partner_whieda") == "already"  # повтор «Да»
            assert await service.promote("whieda", "telegram", 7001, "partner_whieda") == "already"  # партнёра WWC не понижает
            assert await column_of("igor-actor") == "partner_wwc"
            # человека, которого бот ещё не знал, подтверждают и заводят строку
            assert await service.promote("whieda", "telegram", 8800, "partner_whieda") == "changed"
            assert await stage("telegram", 8800) == "partner_whieda"
            assert await column_of("telegram:whieda:8800") == "partner_whieda"
            assert await service.promote("whieda", "max", 9400, "partner_whieda") == "changed"  # Max, актора не было
            assert await stage("max", 9400) == "partner_whieda"
            with pytest.raises(ValueError):
                await service.promote("whieda", "telegram", 7100, "guest")
            with pytest.raises(ValueError):
                await service.promote("whieda", "whatsapp", 7100, "partner_whieda")

            # --- снять ---
            assert await service.demote_to_guest("whieda", "telegram", 7001) == "has_site"
            assert await stage("telegram", 7001) == "partner_wwc"
            assert await service.demote_to_guest("whieda", "telegram", 7100) == "changed"
            assert await stage("telegram", 7100) == "guest"
            assert await service.demote_to_guest("whieda", "telegram", 7100) == "already"

            # --- поиск человека словом владельца ---
            by_name = await service.find_telegram_person("whieda", "@anna_guest")   # ник в display_name
            assert [row["telegram_user_id"] for row in by_name] == [7100]
            assert [row["telegram_user_id"] for row in await service.find_telegram_person("whieda", "@IGOR_WWC")] == [7001]
            assert [row["telegram_user_id"] for row in await service.find_telegram_person("whieda", "7200")] == [7200]
            assert await service.find_telegram_person("whieda", "@nobody") == []
            assert await service.find_telegram_person("whieda", "99999") == []
            assert [row["telegram_user_id"] for row in await service.find_telegram_person("other", "7001")] == [7001]
            assert await service.find_telegram_person("other", "@igor_wwc") == []  # ник — в строке тенанта whieda

            # --- кто пригласил ---
            async with tenant_connection("whieda") as c:
                await c.execute(
                    "insert into referral_invite_codes (tenant_id, inviter_actor_id, invite_code, active) "
                    "values ('whieda', 'igor-actor', 'igor-invite-0001', true)"
                )
                await c.execute(
                    "insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source) "
                    "values ('whieda', 'telegram:whieda:7100', 'igor-actor', 'igor-invite-0001', 'telegram_deeplink')"
                )
            async with tenant_connection("whieda") as c:
                assert await service.inviter_actor_id(c, "whieda", "telegram", 7100) == "igor-actor"
                assert await service.inviter_actor_id(c, "whieda", "telegram", 7200) is None

        db.run_with_app(proof)

        # --- база старее кода: колонки bot_stage ещё нет (владелец таблицы убирает её, роль API не может) ---
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            conn.execute("alter table lead_actors drop constraint lead_actors_bot_stage_check")
            conn.execute("alter table lead_actors drop column bot_stage")

        async def without_the_column() -> None:
            from app.bot_stages import service

            assert await service.person_stage("whieda", "telegram", 7300) == "partner_wwc"   # заявка в очереди и выключенный профиль
            assert await service.person_stage("whieda", "telegram", 7302) == "partner_wwc"   # выключенный профиль
            assert await service.person_stage("whieda", "telegram", 7303) == "guest"         # анкета без оплаты

            assert await service.person_stage("whieda", "telegram", 7001) == "partner_wwc"   # по профилю сайта
            assert await service.person_stage("whieda", "telegram", 7100) == "guest"
            assert await service.person_stage("whieda", "max", 9301) == "partner_wwc"
            assert await service.person_stage("whieda", "telegram", 5000) == "partner_wwc"   # владелец

        db.run_with_app(without_the_column)
