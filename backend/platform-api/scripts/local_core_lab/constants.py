"""Paths and constants for local Core E2E lab."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PLATFORM_API = ROOT / "backend" / "platform-api"
SCRIPTS = PLATFORM_API / "scripts"

POSTGRES_COMPOSE = ROOT / "postgres" / "docker-compose.local-staging.yml"
CORE_COMPOSE = PLATFORM_API / "docker-compose.local-core.yml"
ENV_EXAMPLE = PLATFORM_API / ".env.local.example"
ENSURE_CORE_DB = ROOT / "postgres" / "scripts" / "ensure_local_core_database.py"
STAGING_PROOF = ROOT / "postgres" / "scripts" / "run_local_staging_proof.py"
HTTP_SMOKE = SCRIPTS / "local_http_contract_smoke.py"
VERIFY_E2E = SCRIPTS / "verify_local_core_e2e.py"

ACCEPTANCE_ROOT = ROOT / "qa" / "acceptance"
ACCEPTANCE_RUNNER = ACCEPTANCE_ROOT / "run_acceptance.py"
LOCAL_CORE_SMOKE_CORPUS = ACCEPTANCE_ROOT / "local_core_seed_smoke_v1.jsonl"
ACCEPTANCE_LOCAL_TARGET = ACCEPTANCE_ROOT / "acceptance_target.local.json"
ACCEPTANCE_EXAMPLE_TARGET = ACCEPTANCE_ROOT / "acceptance_target.example.json"

E2E_REPORTS_DIR = PLATFORM_API / "reports" / "local_core_e2e"

LOCAL_STAGING_PORT = 55432
API_PORT = 8080
API_BASE = f"http://127.0.0.1:{API_PORT}"

DOCKER_CONTAINER = "whieda-local-staging-postgres"
CORE_CONTAINER = "whieda-local-core-api"
LOCAL_CORE_DB = "whieda_platform_local_core"
LOCAL_CORE_API_ROLE = "whieda_platform_api_local"

SQL_DIR = ROOT / "postgres" / "sql"

# Import staging apply order from postgres scripts (read-only metadata).
_POSTGRES_SCRIPTS = ROOT / "postgres" / "scripts"
if str(_POSTGRES_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_POSTGRES_SCRIPTS))

from staging_proof_lib import APPLY_ORDER, SEED  # noqa: E402
