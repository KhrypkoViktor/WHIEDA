"""Gate L: local Telegram canary runner guards and evidence evaluation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = ROOT / "backend" / "platform-api"
LAB = PLATFORM / "scripts" / "run_local_core_lab.py"
CANARY_COMPOSE = PLATFORM / "docker-compose.local-telegram-canary.yml"

sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "scripts"))

from app.telegram.api_base import (  # noqa: E402
    TelegramApiBaseError,
    is_local_telegram_api_base,
    resolve_telegram_api_base,
)
from local_core_lab.refuse import refuse_lab_flags  # noqa: E402
from local_core_lab.telegram_canary_eval import (  # noqa: E402
    evaluate_case_evidence,
    photo_then_text_ok,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAB), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_lab_help_lists_canary_flag():
    proc = _run("--help")
    assert proc.returncode == 0
    assert "--tenant-telegram-canary" in proc.stdout
    assert "--e2e" in proc.stdout


def test_runner_refuses_apply_without_starting_network():
    proc = _run("--e2e", "--tenant-telegram-canary", "--apply")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout.strip().splitlines()[0])
    assert payload["code"] == "action_refused"
    combined = (proc.stdout + proc.stderr).lower()
    assert "compose up" not in combined
    assert "docker compose" not in combined


def test_runner_refuses_non_local_dsn_without_starting_network():
    proc = _run(
        "--e2e",
        "--tenant-telegram-canary",
        "--dsn",
        "postgresql://user:pass@db.supabase.co:5432/postgres",
    )
    assert proc.returncode == 1
    assert "action_refused" in proc.stdout
    assert "compose up" not in (proc.stdout + proc.stderr).lower()


def test_runner_refuses_non_local_base_url():
    proc = _run("--e2e", "--tenant-telegram-canary", "--base-url", "https://wwc.best")
    assert proc.returncode == 1
    assert "Refusing non-local" in proc.stdout


def test_refuse_helper_allows_clean_canary_flags():
    assert refuse_lab_flags(apply=False, publish=False, dsn=None, base_url=None) is None


def test_missing_capture_makes_e2e_fail():
    case = {
        "case_id": "L1",
        "status": "PASS",
        "require_capture": True,
        "capture": [],
        "token": "north-token-local",
        "require_photo_then_text": True,
    }
    assert evaluate_case_evidence(case) == "FAIL"


def test_wrong_photo_text_order_makes_e2e_fail():
    events = [
        {"token": "north-token-local", "bot_method": "sendMessage", "payload": {"text": "hi"}},
        {
            "token": "north-token-local",
            "bot_method": "sendPhoto",
            "payload": {"photo": "https://media.example.org/media/tenant-north/SHARE-01/north.webp"},
        },
    ]
    assert photo_then_text_ok(events, token="north-token-local") is False
    case = {
        "case_id": "L9",
        "status": "PASS",
        "require_capture": True,
        "require_photo_then_text": True,
        "token": "north-token-local",
        "capture": events,
    }
    assert evaluate_case_evidence(case) == "FAIL"


def test_zero_inbox_count_is_not_treated_as_missing():
    case = {
        "case_id": "L11",
        "status": "PASS",
        "capture": [],
        "inbox_count": 0,
        "inbox_actual": 0,
        "outbox_count": 0,
        "outbox_actual": 0,
    }
    assert evaluate_case_evidence(case) == "PASS"


def test_retry_then_success_keeps_photo_then_text():
    events = [
        {"token": "north-token-local", "bot_method": "sendPhoto", "payload": {}, "status": 502},
        {
            "token": "north-token-local",
            "bot_method": "sendPhoto",
            "payload": {"photo": "https://media.example.org/p.webp"},
            "status": 200,
        },
        {"token": "north-token-local", "bot_method": "sendMessage", "payload": {"text": "card"}, "status": 200},
    ]
    assert photo_then_text_ok(events, token="north-token-local") is True
    events = [
        {
            "token": "north-token-local",
            "bot_method": "sendPhoto",
            "payload": {"photo": "https://media.example.org/p.webp"},
        },
        {"token": "north-token-local", "bot_method": "sendMessage", "payload": {"text": "card"}},
    ]
    assert photo_then_text_ok(events, token="north-token-local") is True


def test_canary_compose_and_env_stay_local():
    compose = CANARY_COMPOSE.read_text(encoding="utf-8").lower()
    for frag in ("supabase", "duckdns.org", "185.252.", "api.telegram.org"):
        assert frag not in compose
    assert "host.docker.internal" in compose
    assert "18081" in compose
    assert "north-token-local" in compose
    assert "whieda_platform_telegram_canary" in compose


def test_telegram_api_base_default_and_local_only():
    assert resolve_telegram_api_base(None) == "https://api.telegram.org"
    assert is_local_telegram_api_base("http://127.0.0.1:18081")
    assert is_local_telegram_api_base("http://host.docker.internal:18081")
    try:
        resolve_telegram_api_base("https://api.telegram.org")
        raise AssertionError("official override must be refused when set explicitly")
    except TelegramApiBaseError:
        pass
    try:
        resolve_telegram_api_base("https://example.com")
        raise AssertionError("public override must be refused")
    except TelegramApiBaseError:
        pass
