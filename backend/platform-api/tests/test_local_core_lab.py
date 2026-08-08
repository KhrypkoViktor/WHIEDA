"""Static guards for local Core runtime lab files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = ROOT / "backend" / "platform-api"
CORE_COMPOSE = PLATFORM / "docker-compose.local-core.yml"
ENV_EXAMPLE = PLATFORM / ".env.local.example"
LAB_SCRIPT = PLATFORM / "scripts" / "run_local_core_lab.py"
HTTP_SMOKE = PLATFORM / "scripts" / "local_http_contract_smoke.py"

FORBIDDEN_FRAGMENTS = (
    "supabase",
    "duckdns.org",
    "185.252.",
    "sysarchn8n",
    "api.telegram.org",
    "PLATFORM_TELEGRAM_BOT_TOKEN=",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)

SECRET_ASSIGNMENT = re.compile(
    r"^(PLATFORM_TELEGRAM_BOT_TOKEN|PLATFORM_TELEGRAM_WEBHOOK_SECRET|OPENAI_API_KEY)=",
    re.I,
)


def test_core_compose_has_no_prod_urls():
    text = CORE_COMPOSE.read_text(encoding="utf-8").lower()
    for frag in FORBIDDEN_FRAGMENTS:
        assert frag not in text, frag
    assert "8080:8080" in text
    assert ".env.local.example" in text


def test_env_local_example_is_localhost_only():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for frag in FORBIDDEN_FRAGMENTS:
        assert frag not in text.lower(), frag
    for line in text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        assert SECRET_ASSIGNMENT.search(line) is None
    assert "127.0.0.1" in text
    assert "host.docker.internal" in text
    assert "55432" in text


def test_http_smoke_does_not_call_telegram_or_n8n():
    text = HTTP_SMOKE.read_text(encoding="utf-8")
    # External hosts may appear in response guard lists, not as outbound request targets.
    assert re.search(
        r'request\s*\(\s*"[^"]+"\s*,\s*f?"https?://(?!127\.0\.0\.1|localhost)',
        text,
    ) is None
    assert "/v1/telegram/webhook" not in text
    assert "duckdns.org/webhook" not in text
    assert "Refusing non-local" in text
    assert "127.0.0.1" in text


def test_lab_script_does_not_remove_postgres_volume():
    text = LAB_SCRIPT.read_text(encoding="utf-8")
    lower = text.lower()
    assert "volume rm" not in lower
    assert "down --volumes" not in lower
    assert "down -v" not in lower
    assert "run_local_staging_proof.py" in text
    assert "ensure_local_core_database.py" in text


def test_lab_script_requires_docker():
    proc = subprocess.run(
        [sys.executable, str(LAB_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0


def test_ensure_core_db_skips_reapply_when_initialized():
    text = (ROOT / "postgres" / "scripts" / "ensure_local_core_database.py").read_text(encoding="utf-8")
    assert "_schema_initialized()" in text
    assert "--force-reapply" in text
    assert "already initialized" in text


def test_http_smoke_checks_telegram_webhook_entry():
    text = HTTP_SMOKE.read_text(encoding="utf-8")
    assert "check_legacy_webhook_not_used" in text
    assert "/v1/telegram/local-lab-smoke/webhook" in text
    proc = subprocess.run(
        [sys.executable, str(HTTP_SMOKE), "--base-url", "https://wwc.best"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Refusing non-local" in proc.stderr + proc.stdout
