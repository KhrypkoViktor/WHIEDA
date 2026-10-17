"""Почта для заявок (17.10.2026) на настоящем PostgreSQL: тот же SQL, что выполняет бот.

  * запись — ``jsonb_set`` по одному ключу: имя, «о себе», контакты и тема остаются; profile_version не растёт;
  * убрать — ключ уходит, остальное цело; повторное «убрать» ничего не меняет;
  * выключенный сайт и сайт чужого тенанта (RLS) не затрагиваются; профиль-не-объект не ломает запись;
  * владелец: общий поиск «кода входа» (ref → id → имя) и поиск по профилю для партнёра без telegram_user_id;
  * бот целиком: партнёр и владелец за партнёра, ответы и /me (lead_email_of).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

OWNER, VOLKOVA, PETROVA, PLAIN = 2, 700, 701, 703

SEED = """
insert into tenants (tenant_id, display_name, status) values ('other', 'Other', 'active')
on conflict (tenant_id) do nothing;
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('viktor', 'whieda', 'Виктор', '2'),
  ('volkova', 'whieda', 'Евгения Волкова', '700'),
  ('petrova', 'whieda', 'Евгения Петрова', '701'),
  ('plain', 'whieda', 'Без сайта', '703'),
  ('ghost', 'whieda', 'Светлана Призрак', null),
  ('weird', 'whieda', 'Кривой профиль', null),
  ('o-volkova', 'other', 'Чужая Волкова', '900');
update lead_actors set telegram_user_id = 2 where actor_id = 'viktor';
update lead_actors set telegram_user_id = 700 where actor_id = 'volkova';
update lead_actors set telegram_user_id = 701 where actor_id = 'petrova';
update lead_actors set telegram_user_id = 703 where actor_id = 'plain';
update lead_actors set telegram_user_id = 900 where actor_id = 'o-volkova';
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('volkova', 'whieda', 'volkova', 'named',
   '{"display_name": "Евгения Волкова", "bio": "О себе", "contacts": {"phone": "+7 900"}, "selected_theme_id": "gold"}', true),
  ('volkova2', 'whieda', 'volkova', 'named', '{"display_name": "Евгения Волкова"}', true),
  ('petrova', 'whieda', 'petrova', 'named', '{"display_name": "Евгения Петрова", "lead_email": "old@x.ru"}', true),
  ('viktor', 'whieda', 'viktor', 'named', '{"display_name": "Виктор"}', true),
  ('ghost', 'whieda', 'ghost', 'named', '{"display_name": "Светлана Призрак"}', true),
  ('weird', 'whieda', 'weird', 'named', '[]', true),
  ('off', 'whieda', 'plain', 'named', '{"display_name": "Закрытый", "lead_email": "off@x.ru"}', false),
  ('o-volkova', 'other', 'o-volkova', 'named', '{"display_name": "Чужая Волкова"}', true);
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "")


@pytest.mark.integration
def test_lead_email_end_to_end(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_lead_email") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=False)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from types import SimpleNamespace

            from app.db import tenant_connection
            from app.lead_email import bot, service
            from app.login_identities.rules import LoginIdentityError
            from app.telegram.update_parser import TelegramMessage

            async def profile(ref: str, tenant: str = "whieda") -> dict:
                async with tenant_connection(tenant) as conn:
                    cur = await conn.execute(
                        "select public_profile, profile_version, updated_at from referral_profiles where ref_code = %s", (ref,)
                    )
                    return await cur.fetchone()

            # --- чтение: сайты человека -----------------------------------------------------------------
            assert [s.ref_code for s in await service.sites_of_telegram("whieda", VOLKOVA)] == ["volkova", "volkova2"]
            assert await service.sites_of_telegram("whieda", PLAIN) == [], "выключенный сайт — не сайт"
            assert await service.sites_of_telegram("whieda", 999) == []
            assert [(s.ref_code, s.lead_email) for s in await service.sites_of_telegram("whieda", PETROVA)] == [("petrova", "old@x.ru")]
            assert await service.lead_email_of("whieda", PETROVA) == "old@x.ru"
            assert await service.lead_email_of("whieda", VOLKOVA) is None and await service.lead_email_of("whieda", 999) is None
            assert await service.lead_email_of("other", PETROVA) is None, "чужой тенант своих строк не видит"

            # --- запись: только ключ lead_email ---------------------------------------------------------
            before = await profile("volkova")
            changed = await service.set_lead_email("whieda", ["volkova", "volkova2"], "Ivan@gmail.com", telegram_user_id=VOLKOVA, by="self")
            assert changed == ["volkova", "volkova2"]
            after = await profile("volkova")
            assert after["public_profile"] == {**before["public_profile"], "lead_email": "Ivan@gmail.com"}, "остальной профиль цел"
            assert after["profile_version"] == before["profile_version"], "сайт не пересобирается"
            assert after["updated_at"] > before["updated_at"]
            assert (await profile("volkova2"))["public_profile"]["lead_email"] == "Ivan@gmail.com"
            assert (await profile("petrova"))["public_profile"]["lead_email"] == "old@x.ru", "чужой профиль не тронут"
            assert await service.lead_email_of("whieda", VOLKOVA) == "Ivan@gmail.com"

            # --- убрать ---------------------------------------------------------------------------------
            assert await service.clear_lead_email("whieda", ["volkova", "volkova2", "petrova2"], telegram_user_id=VOLKOVA, by="self") == ["volkova", "volkova2"]
            assert (await profile("volkova"))["public_profile"] == before["public_profile"]
            assert await service.clear_lead_email("whieda", ["volkova", "volkova2"], telegram_user_id=VOLKOVA, by="self") == [], "повторно — ничего"
            assert await service.lead_email_of("whieda", VOLKOVA) is None

            # --- границы: выключенный, чужой тенант, профиль-не-объект -----------------------------------------
            assert await service.set_lead_email("whieda", ["off"], "a@b.c", telegram_user_id=PLAIN, by="self") == []
            assert (await profile("off"))["public_profile"]["lead_email"] == "off@x.ru", "выключенный сайт не меняется"
            assert await service.set_lead_email("other", ["volkova"], "a@b.c", telegram_user_id=None, by="owner") == [], "ref из другого тенанта"
            assert "lead_email" not in (await profile("volkova"))["public_profile"]
            assert await service.set_lead_email("other", ["o-volkova"], "o@b.c", telegram_user_id=900, by="self") == ["o-volkova"]
            assert "lead_email" not in (await profile("volkova", "whieda"))["public_profile"]
            assert await service.set_lead_email("whieda", ["weird"], "w@b.c", telegram_user_id=None, by="owner") == ["weird"]
            assert (await profile("weird"))["public_profile"] == {"lead_email": "w@b.c"}, "не-объект заменён объектом, а не упал"
            assert await service.clear_lead_email("whieda", ["weird"], telegram_user_id=None, by="owner") == ["weird"]
            assert (await profile("weird"))["public_profile"] == {}

            # --- «кто» владельца: общий поиск «кода входа», затем профиль без telegram_user_id -------------------
            target = await service.resolve_owner_target("whieda", "VOLKOVA")
            assert (target.title, target.telegram_user_id, [s.ref_code for s in target.sites]) == ("Евгения Волкова (volkova)", VOLKOVA, ["volkova", "volkova2"])
            target = await service.resolve_owner_target("whieda", str(PETROVA))
            assert target.title == "Евгения Петрова (petrova)" and target.sites[0].lead_email == "old@x.ru"
            target = await service.resolve_owner_target("whieda", "Петрова")
            assert target.telegram_user_id == PETROVA
            target = await service.resolve_owner_target("whieda", str(PLAIN))
            assert target.title == "Без сайта" and target.sites == [], "человек есть, сайта нет"
            target = await service.resolve_owner_target("whieda", "ghost")
            assert (target.title, target.telegram_user_id, [s.ref_code for s in target.sites]) == ("Светлана Призрак (ghost)", None, ["ghost"])
            target = await service.resolve_owner_target("whieda", "Призрак")
            assert target.sites[0].ref_code == "ghost", "по имени — тоже"
            with pytest.raises(LoginIdentityError) as info:
                await service.resolve_owner_target("whieda", "Евгения")
            assert info.value.code == "partner_ambiguous" and sorted(p["ref_code"] for p in info.value.details["candidates"]) == ["petrova", "volkova", "volkova2"], "по имени — строка на сайт"
            with pytest.raises(LoginIdentityError) as info:
                await service.resolve_owner_target("whieda", "никого")
            assert info.value.code == "partner_not_found"
            target = await service.resolve_owner_target("whieda", "off")
            assert target.title == "Закрытый (off)" and target.sites == [], "человек найден, но сайт выключен — писать некуда"
            with pytest.raises(LoginIdentityError) as info:
                await service.resolve_owner_target("whieda", "o-volkova")
            assert info.value.code == "partner_not_found", "ref чужого тенанта"

            # --- бот целиком, на настоящей базе ----------------------------------------------------------------
            tenant = SimpleNamespace(tenant_id="whieda")
            sent: list[str] = []

            def message(text: str, user_id: int) -> TelegramMessage:
                return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type="private", file_id=None, raw={})

            async def say(text: str, user_id: int):
                return await bot.try_handle_lead_email_message(tenant, message(text, user_id), trace_id="t")

            with patch.object(bot, "_deliver", AsyncMock(side_effect=lambda chat, text: sent.append(text))):
                assert (await say("почта для заявок Ivan@Gmail.COM", VOLKOVA))["status"] == "set"
                assert sent[-1] == "Готово. Заявки с сайта будут приходить и на Ivan@gmail.com. Убрать: «почта убрать»."
                assert (await say("/leademail", VOLKOVA))["status"] == "shown" and "Ivan@gmail.com" in sent[-1]
                assert (await say("почта", PLAIN))["status"] == "no_site" and "Заказать сайт WWC" in sent[-1]
                assert (await say("почта ivan@", VOLKOVA))["status"] == "invalid_email"
                assert (await profile("volkova"))["public_profile"]["lead_email"] == "Ivan@gmail.com", "неверный адрес прежний не затирает"

                # партнёр без Telegram: владелец задаёт по ref, потом по имени убирает
                assert (await say("почта для заявок ghost svetlana@mail.ru", OWNER))["status"] == "set"
                assert sent[-1] == "Почта для заявок у Светлана Призрак (ghost): svetlana@mail.ru"
                assert (await profile("ghost"))["public_profile"] == {"display_name": "Светлана Призрак", "lead_email": "svetlana@mail.ru"}
                assert (await say("почта для заявок Призрак убрать", OWNER))["status"] == "cleared"
                assert (await profile("ghost"))["public_profile"] == {"display_name": "Светлана Призрак"}

                # владельцу — не записывается; не-владельцу «кто» не команда
                assert (await say("почта для заявок volkova boss@b.c", OWNER))["status"] == "set"
                assert "lead_email" not in (await profile("viktor"))["public_profile"]
                assert (await profile("volkova"))["public_profile"]["lead_email"] == "boss@b.c"
                assert await say("почта для заявок petrova hack@b.c", VOLKOVA) is None
                assert (await profile("petrova"))["public_profile"]["lead_email"] == "old@x.ru"

                assert (await say("почта убрать", VOLKOVA))["status"] == "cleared"
                assert "lead_email" not in (await profile("volkova"))["public_profile"]

        db.run_with_app(proof)
