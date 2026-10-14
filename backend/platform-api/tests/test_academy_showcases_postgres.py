"""Витрины Академии (V29, 11.10.2026) на настоящем PostgreSQL.

  * миграция повторяется без ошибок, витрина платформы заводится сама, RLS по тенанту;
  * порядок и состав курсов зависят от витрины, замки — нет;
  * хосты витрин разрешены во входе (return_to, ссылка из бота), чужие — нет;
  * бот входа, ссылки уведомлений и контакт автора — витрины автора курса;
  * партнёр со своей витриной — автор без оплаты «Академии автора».
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi import HTTPException

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V29 = "platform_academy_showcases_v29.sql"
ADMIN, VIKTOR, SASHA, STUDENT, STRANGER = 1, 2, 7001, 9001, 9002

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('viktor', 'whieda', 'Виктор', '2'),
  ('almira', 'whieda', 'Александра Пармон', '7001'),
  ('masha', 'whieda', 'Мария', '9001');
update lead_actors set telegram_user_id = 2 where actor_id = 'viktor';
update lead_actors set telegram_user_id = 7001 where actor_id = 'almira';
update lead_actors set telegram_user_id = 9001 where actor_id = 'masha';

insert into academy_courses (tenant_id, slug, title, access_rule, author_actor_id, status) values
  ('whieda', 'zapusk', 'Запуск WWC', 'pro', null, 'published'),
  ('whieda', 'online', 'Онлайн-старт', 'purchase', 'viktor', 'published'),
  ('whieda', 'kompoziciya', 'Композиция', 'purchase', 'almira', 'published');
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html)
select 'whieda', course_id, 'urok-1', 1, 'Урок 1', '<p>1</p>' from academy_courses;

-- Ученица купила «Композицию» и «Онлайн-старт».
insert into academy_access (tenant_id, course_id, telegram_user_id, source)
select 'whieda', course_id, 9001, 'key' from academy_courses where slug in ('kompoziciya', 'online');

-- Колонки бота (platform_bot_binding_context_v1.sql) — не в наборе тестовых миграций.
alter table tenant_bot_bindings
  add column if not exists bot_token_ref text,
  add column if not exists bot_username text;
insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status)
values ('almira-academy-bot', 'whieda', 'env:ALMIRA_SECRET', 'env:ALMIRA_TOKEN', 'ALMIRA_I_bot', 'active');

insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               theme, assets, links, telegram_binding_id, more_mode)
values ('whieda', 'almira', array['academy.almira.by', 'almira.wwc.best'], 'almira', 'ALMIRA',
        'Онлайн-академия ALMIRA', '{"--gold": "#E4541B", "--bad": "url(javascript:x)", "color": "red"}',
        '{"logo_url": "/academy/brands/almira/logo.png", "evil": "x"}',
        '{"home_url": "https://almira.by/", "contact_telegram": "alm_school",
          "contact_url": "https://t.me/alm_school", "privacy_url": "https://almira.by/privacy-policy/"}',
        'almira-academy-bot', 'none');
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(ADMIN))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(ADMIN))
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)


@pytest.mark.integration
def test_v29_twice_seeds_platform_showcase_and_isolates_tenants():
    assert V29 in MIGRATIONS
    sql = (SQL_DIR / V29).read_text(encoding="utf-8")
    assert "$" not in sql  # боевые правки идут через n8n, он режет знак доллара
    with temporary_database("whieda_showcase_v29") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(sql)
            assert conn.execute(
                "select showcase_id, is_default, more_mode, hosts from academy_showcases"
            ).fetchall() == [("wwc", True, "none", [])]
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into academy_showcases (tenant_id, showcase_id, is_default, brand_name, academy_title)"
                    " values ('whieda', 'second', true, 'X', 'X')"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into academy_showcases (tenant_id, showcase_id, brand_name, academy_title, more_mode)"
                    " values ('whieda', 'x', 'X', 'X', 'everything')"
                )
            db.grant_api_role(conn)
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            assert api.execute("select count(*) from academy_showcases").fetchone() == (0,)
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select count(*) from academy_showcases").fetchone() == (1,)


@pytest.mark.integration
def test_showcases_order_courses_open_login_and_route_links(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_showcases") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy.author import author_courses
            from app.academy.keys import issue_keys_for_telegram
            from app.academy.notify import lesson_page_url
            from app.academy.service import list_courses, load_viewer
            from app.content_access.service import sanitize_return_to
            from app.telegram.site_login import _is_site

            sc._reset_cache()
            wwc = await sc.showcase_for_host("whieda", "wwc.best")
            almira = await sc.showcase_for_host("whieda", "academy.almira.by")
            assert (wwc.showcase_id, wwc.is_default) == ("wwc", True)
            assert almira.showcase_id == "almira"
            assert (await sc.showcase_for_host("whieda", "Almira.WWC.best:443")).showcase_id == "almira"
            assert (await sc.showcase_for_host("whieda", "olesya.wwc.best")).showcase_id == "wwc"
            # Чужие ключи темы и опасные значения отброшены.
            assert almira.theme == {"--gold": "#E4541B"}
            assert almira.assets == {"logo_url": "/academy/brands/almira/logo.png"}
            assert almira.base_url == "https://academy.almira.by"

            student = await load_viewer("whieda", STUDENT)
            stranger = await load_viewer("whieda", STRANGER)

            # 1. Витрина платформы: свои — курсы без своей витрины; «Композиция» — только купившей.
            rows = await list_courses("whieda", student, wwc)
            assert [(r["slug"], r["section"]) for r in rows] == [
                ("zapusk", "own"), ("online", "own"), ("kompoziciya", "more")
            ]
            assert [r["slug"] for r in await list_courses("whieda", stranger, wwc)] == ["zapusk", "online"]

            # 2. Витрина ALMIRA (more_mode none): только её курсы — купленный чужой тоже скрыт
            # (владелец 14.10: «мои курсы из Алмиры скрой»); он остаётся на витрине WWC.
            rows = await list_courses("whieda", student, almira)
            assert [(r["slug"], r["section"], r["locked"]) for r in rows] == [("kompoziciya", "own", False)]
            assert "online" in [r["slug"] for r in await list_courses("whieda", student, wwc)]
            [locked] = await list_courses("whieda", stranger, almira)
            assert (locked["slug"], locked["lock_reason"]) == ("kompoziciya", "purchase_required")
            # Контакт автора с витриной — студия, а не личный аккаунт.
            assert locked["author_contact"] == {"telegram": "alm_school", "site_url": "https://almira.by/"}

            # 3. no_pro: чужие курсы без PRO; замки не меняются.
            no_pro = sc.Showcase(**{**almira.__dict__, "more_mode": "no_pro"})
            rows = await list_courses("whieda", stranger, no_pro)
            assert [(r["slug"], r["section"], r["lock_reason"]) for r in rows] == [
                ("kompoziciya", "own", "purchase_required"), ("online", "more", "purchase_required")
            ]

            # 4. Вход: адрес витрины разрешён после прогрева списка, чужой — нет.
            await sc.warm_hosts("whieda")
            assert sanitize_return_to("https://academy.almira.by/academy/?course=kompoziciya").startswith(
                "https://academy.almira.by/academy/"
            )
            with pytest.raises(HTTPException):
                sanitize_return_to("https://evil.almira.by/academy/")
            assert _is_site("https://academy.almira.by/academy/") and not _is_site("https://almira.by/")
            assert await sc.login_bot_username(almira) == "ALMIRA_I_bot"
            assert await sc.login_bot_username(wwc) == "WHIEDA_Advisor_bot"
            assert (await sc.showcase_for_binding("whieda", "almira-academy-bot")).showcase_id == "almira"
            assert await sc.showcase_for_binding("whieda", "whieda-advisor-bot") is None

            # 5. Ссылки уведомлений — на витрину автора курса.
            course_showcase = await sc.showcase_for_course("whieda", "kompoziciya")
            assert lesson_page_url("kompoziciya", "urok-1", course_showcase.base_url) == (
                "https://academy.almira.by/academy/?course=kompoziciya&lesson=urok-1"
            )
            assert (await sc.showcase_for_course("whieda", "online")).base_url == "https://wwc.best"

            # 6. Партнёр без строки academy_shelf: кабинет автора и ключи работают.
            sasha = await load_viewer("whieda", SASHA)
            cabinet = await author_courses("whieda", sasha)
            assert [c["slug"] for c in cabinet["courses"]] == ["kompoziciya"]
            assert cabinet["shelf"]["active"] and cabinet["shelf"]["partner"]
            issued = await issue_keys_for_telegram("whieda", "kompoziciya", SASHA, 2, is_admin=False)
            assert len(issued.codes) == 2

        db.run_with_app(proof)
