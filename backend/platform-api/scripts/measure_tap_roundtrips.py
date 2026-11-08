"""Сколько транзакций и обращений к PostgreSQL уходит на одно нажатие кнопки бота (04.11.2026).

Запуск из каталога backend/platform-api:
    PYTHONUTF8=1 python scripts/measure_tap_roundtrips.py [метка] > measure.json
Нужен PostgreSQL (scoop, EDB или PG_BIN): поднимается временный локальный кластер на свободном порту
с log_statement=all — одна строка лога = одно обращение к серверу по проводу (BEGIN, set_config,
запросы, COMMIT — каждое отдельно, как у psycopg без конвейера). Bot API подменён, секретов нет.

Сценарии: svc:card:gemini, svc:order:gemini_6m, svc:confirm:gemini_6m, site:skip (два шага подряд);
плюс привязка бота (resolve_bot_binding_context), которая идёт в вебхуке до обработки.
В выводе: на сценарий — транзакции, запросы, обращения по проводу (по логу сервера), вызовы Bot API
по порядку и таблица «кто открыл транзакцию».
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

LABEL = sys.argv[1] if len(sys.argv) > 1 else "run"
# warm — кэши процесса (привязка бота, форум поддержки) уже тёплые: обычный случай на бою;
# cold — перед каждым нажатием кэши пусты: первое нажатие после перезапуска контейнера.
CACHES = sys.argv[2] if len(sys.argv) > 2 else "warm"

# (имя в выводе, callback_data, message_id сообщения с кнопкой). Первые пять — сценарии лида;
# остальные — другие семьи кнопок бота, чтобы видеть, где в цепочке больше всего обращений.
SCENARIOS = (
    ("svc:card:gemini", "svc:card:gemini", 100),
    ("svc:order:gemini_6m", "svc:order:gemini_6m", 101),
    ("svc:confirm:gemini_6m", "svc:confirm:gemini_6m", 102),
    ("site:skip (шаг фото)", "site:skip", 103),
    ("site:skip (шаг текст)", "site:skip", 104),
    ("acad:home", "acad:home", 105),
    ("referral:history", "referral:history", 106),
    ("referral:list", "referral:list", 107),
    ("nav:menu", "nav:menu", 108),
    ("nav:products", "nav:products", 109),
    ("nc:start", "nc:start", 110),
)


def _pg_bin() -> Path:
    candidates = [os.environ.get("PG_BIN", ""), str(Path.home() / "scoop/apps/postgresql/current/bin")]
    candidates += [str(p) for p in sorted(Path("C:/Program Files/PostgreSQL").glob("*/bin"), reverse=True)]
    for candidate in candidates:
        if candidate and (Path(candidate) / "pg_ctl.exe").exists():
            return Path(candidate)
    raise SystemExit("PostgreSQL binaries not found: set PG_BIN")


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wire_statements(log: Path, start: int) -> list[str]:
    """Обращения к серверу по логу с позиции ``start``."""
    time.sleep(0.4)
    lines = log.read_bytes()[start:].decode("utf-8", "replace").splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        match = re.search(r"LOG:\s+(?:statement|execute [^:]*):\s*(.*)", line)
        if not match:
            continue
        sql = match.group(1).strip()
        follow = index + 1
        while not sql and follow < len(lines) and not re.match(r"\d{4}-", lines[follow]):
            sql = lines[follow].strip()
            follow += 1
        found.append(" ".join(sql.split())[:60])
    return found


def _step_of(frames: tuple[str, ...]) -> str:
    """Шаг цепочки: функция, которую вызвала _process_core_telegram_update_scoped; иначе вебхук."""
    marker = "telegram.processor._process_core_telegram_update_scoped"
    if marker in frames:
        index = frames.index(marker)
        return frames[index - 1] if index else marker
    if any(name.endswith("resolve_bot_binding_context") for name in frames):
        return "webhook: resolve_bot_binding_context"
    return frames[0] if frames else "?"


def main() -> None:
    pg = _pg_bin()
    cluster = Path(tempfile.mkdtemp(prefix="whieda-tap-measure-"))
    data, log = cluster / "data", cluster / "postgres.log"
    (cluster / "pw.txt").write_text("measure_pw", encoding="ascii")
    port = _free_port()
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    subprocess.run(
        [str(pg / "initdb.exe"), "-D", str(data), "-U", "postgres", f"--pwfile={cluster / 'pw.txt'}", "-E", "UTF8", "-A", "scram-sha-256"],
        check=True, **quiet,
    )
    subprocess.run(
        [str(pg / "pg_ctl.exe"), "-D", str(data), "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c log_statement=all -c lc_messages=C", "-l", str(log), "start"],
        check=True, **quiet,
    )
    results: dict[str, dict] = {}
    try:
        for _ in range(60):
            if subprocess.run([str(pg / "pg_isready.exe"), "-h", "127.0.0.1", "-p", str(port), "-q"]).returncode == 0:
                break
            time.sleep(0.5)
        os.environ["PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN"] = f"postgresql://postgres:measure_pw@127.0.0.1:{port}/postgres"
        os.environ["PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID"] = "2101187096"
        os.environ["PLATFORM_BILLING_OWNER_TELEGRAM_ID"] = "688931415"
        os.environ["TAP_BOT_TOKEN"] = "test-bot-token"
        os.environ["TAP_WEBHOOK_SECRET"] = "tap-secret"

        import psycopg

        from tests import tap_testkit as kit
        from tests.postgres_testkit import MIGRATIONS, temporary_database

        with temporary_database("whieda_tap_measure") as db:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                db.apply_migrations(conn, (*MIGRATIONS, "platform_service_sales_v10.sql"))
                conn.execute(kit.SEED)
                conn.execute("alter table tenant_bot_bindings add column if not exists bot_token_ref text, add column if not exists bot_username text")
                conn.execute(
                    "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status) "
                    "values (%s, 'whieda', 'env:TAP_WEBHOOK_SECRET', 'env:TAP_BOT_TOKEN', 'WHIEDA_Advisor_bot', 'active')",
                    (kit.BINDING_ID,),
                )
                db.grant_api_role(conn)

            async def proof() -> None:
                import httpx

                from app.telegram.bindings import resolve_bot_binding_context
                from app.telegram.routes import _process_telegram_update
                from app.telegram.sequencer import reset_chat_sequencer_for_tests
                from app.ttl_cache import clear_all_caches

                binding = kit.whieda_binding()
                fake = kit.FakeTelegram()
                real_post = httpx.AsyncClient.post

                async def post(client, url, **kwargs):
                    return await fake.post(client, url, **kwargs)

                httpx.AsyncClient.post = post  # type: ignore[method-assign]
                counter = kit.TransactionCounter()
                try:
                    with counter:
                        async def run(name: str, action) -> None:
                            counter.reset()
                            fake.calls.clear()
                            offset = log.stat().st_size
                            started = time.perf_counter()
                            await action()
                            elapsed_ms = (time.perf_counter() - started) * 1000
                            routes = sys.modules.get("app.telegram.routes")
                            while routes is not None and getattr(routes, "_typing_tasks", None):
                                await asyncio.gather(*list(routes._typing_tasks), return_exceptions=True)  # «печатает…» (после правки)
                            wire = _wire_statements(log, offset)
                            steps: dict[str, dict[str, int]] = {}
                            for tx in counter.transactions:
                                step = steps.setdefault(_step_of(tx.frames), {"transactions": 0, "queries": 0})
                                step["transactions"] += 1
                                step["queries"] += tx.queries
                            results[name] = {
                                "transactions": counter.count,
                                "queries": counter.queries,
                                "wire_round_trips": len(wire),
                                "wall_ms_local": round(elapsed_ms),
                                "telegram_calls": fake.methods(),
                                "steps": steps,
                                "transactions_detail": [
                                    {"queries": tx.queries, "opened_by": list(tx.frames[:3])} for tx in counter.transactions
                                ],
                                "wire_statements": wire,
                            }

                        from app.support.service import FORUM_KIND_SERVICES, FORUM_KIND_SITE, get_forum

                        async def warm_caches() -> None:
                            await resolve_bot_binding_context(kit.BINDING_ID)
                            for kind in (FORUM_KIND_SERVICES, FORUM_KIND_SITE):
                                await get_forum("whieda", binding_id=kit.BINDING_ID, kind=kind)

                        clear_all_caches()
                        reset_chat_sequencer_for_tests()
                        await run("webhook_binding", lambda: resolve_bot_binding_context(kit.BINDING_ID))
                        await run("webhook_binding (повторно)", lambda: resolve_bot_binding_context(kit.BINDING_ID))
                        if CACHES == "warm":
                            await warm_caches()
                        for number, (name, data_, message_id) in enumerate(SCENARIOS, start=1):
                            reset_chat_sequencer_for_tests()
                            if CACHES == "cold":
                                clear_all_caches()
                            update = kit.callback_update(data_, update_id=1000 + number, message_id=message_id)
                            await run(name, lambda update=update: _process_telegram_update(binding, update, f"trace-{number}"))
                finally:
                    httpx.AsyncClient.post = real_post  # type: ignore[method-assign]

            os.environ["PLATFORM_DATABASE_URL"] = db.api_dsn
            db.run_with_app(proof)
    finally:
        subprocess.run([str(pg / "pg_ctl.exe"), "-D", str(data), "-m", "fast", "-w", "stop"], **quiet)
        shutil.rmtree(cluster, ignore_errors=True)
    print(json.dumps({"label": LABEL, "results": results}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
