"""«Своя сеть» на настоящей схеме PostgreSQL (11.11.2026): признак ``public_profile->>'own_network'`` читается из jsonb
под ролью API (RLS), включённость профиля и поиск по ``owner_id`` / ``ref_code`` работают так, как описано в app/funnel/network.py."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_crm_postgres import LEAD_TABLES

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name) values
  ('viktor', 'whieda', 'Виктор'), ('own-actor', 'whieda', 'Своя'), ('foreign-actor', 'whieda', 'Чужой'),
  ('text-flag-actor', 'whieda', 'Строка'), ('off-actor', 'whieda', 'Выключен'), ('plain-actor', 'whieda', 'Без признака');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('nnm', 'whieda', 'viktor', 'anonymous', '{}', true),
  ('olga-samtsova', 'whieda', 'own-actor', 'named', '{"own_network": true}', true),
  ('own-second', 'whieda', 'own-actor', 'named', '{}', true),
  ('foreign', 'whieda', 'foreign-actor', 'named', '{"own_network": false}', true),
  ('text-flag', 'whieda', 'text-flag-actor', 'named', '{"own_network": "true"}', true),
  ('off', 'whieda', 'off-actor', 'named', '{"own_network": true}', false),
  ('plain', 'whieda', 'plain-actor', 'named', '{"subdomain": "plain"}', true);
"""


@pytest.mark.integration
def test_own_network_flag_is_read_from_the_profile_json(monkeypatch):
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")
    monkeypatch.delenv("PLATFORM_OWN_NETWORK_REFS", raising=False)
    from app.settings import get_settings

    get_settings.cache_clear()
    with temporary_database("whieda_own_network") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(LEAD_TABLES)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import tenant_connection
            from app.funnel.network import is_own_network

            async def own(**kwargs) -> bool:
                async with tenant_connection("whieda") as conn:
                    return await is_own_network(conn, "whieda", **kwargs)

            assert await own(ref_code="olga-samtsova") is True            # булево true в JSON
            assert await own(owner_id="own-actor") is True                # любой профиль владельца с признаком
            assert await own(ref_code="own-second") is False              # признак читается у профиля того кода, что назван
            assert await own(ref_code="OLGA-SAMTSOVA") is True            # код без учёта регистра
            assert await own(ref_code="foreign") is False                 # false в JSON
            assert await own(owner_id="foreign-actor") is False
            assert await own(ref_code="text-flag") is True                # строка "true" тоже читается (->> даёт текст)
            assert await own(ref_code="plain") is False                   # профиль есть, признака нет — чужой партнёр
            assert await own(owner_id="plain-actor") is False
            assert await own(ref_code="off") is True                      # профиль выключен — партнёра для гостя нет
            assert await own(ref_code="nobody") is True                   # неизвестный код
            assert await own(owner_id="telegram:whieda:77") is True       # актёр без профиля
            assert await own(owner_id="organic") is True                  # владелец «по умолчанию»
            assert await own() is True                                    # гость без партнёра
            assert await own(owner_id="viktor") is False                  # профили Виктора без признака: сайт его ещё не записал

            monkeypatch.setenv("PLATFORM_OWN_NETWORK_REFS", "viktor, plain")
            get_settings.cache_clear()
            assert await own(owner_id="viktor") is True                   # запасной список по owner_id
            assert await own(ref_code="nnm") is False                     # …но не по чужому коду того же владельца: код не в списке
            assert await own(ref_code="plain") is True                    # и по ref_code

        db.run_with_app(proof)
    get_settings.cache_clear()
