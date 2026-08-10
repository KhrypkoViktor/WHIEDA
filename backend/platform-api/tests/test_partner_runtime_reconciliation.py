"""Unit tests for WHIEDA partner runtime reconciliation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "qa" / "partner_runtime" / "fixtures"
N8N_CURRENT = ROOT / "n8n" / "current"
sys.path.insert(0, str(N8N_CURRENT))

from whieda_partner_runtime_reconciliation_lib import (  # noqa: E402
    MasterSourceError,
    apply_plan_in_memory,
    build_deactivation_sql,
    build_master_upsert_sql,
    build_reconciliation_plan,
    load_allowlist_config,
    parse_partners_ref_tsv,
    propose_deactivations,
    runtime_state_from_rows,
    validate_master_rows,
)


@pytest.fixture()
def allowlist():
    return load_allowlist_config(N8N_CURRENT / "whieda_partner_runtime_allowlist.json")


@pytest.fixture()
def master_six_rows():
    return validate_master_rows(parse_partners_ref_tsv(FIXTURES / "master_six_partners.tsv"))


@pytest.fixture()
def runtime_eight_plus_retired():
    payload = json.loads((FIXTURES / "runtime_with_extras.json").read_text(encoding="utf-8"))
    from whieda_partner_runtime_reconciliation_lib import RuntimeActor, RuntimeProfile, RuntimeState

    return RuntimeState(
        actors={
            item["actor_id"]: RuntimeActor(
                actor_id=item["actor_id"],
                display_name=item["display_name"],
                active=item["active"],
            )
            for item in payload["actors"]
        },
        profiles=[
            RuntimeProfile(
                ref_code=item["ref_code"],
                owner_id=item["owner_id"],
                enabled=item["enabled"],
            )
            for item in payload["profiles"]
        ],
    )


def test_master_removal_proposes_disable_not_delete(master_six_rows, runtime_eight_plus_retired, allowlist):
    plan = build_reconciliation_plan(master_six_rows, runtime_eight_plus_retired, allowlist)
    retired = [item for item in plan.proposed_actor_deactivations if item.actor_id == "retired-partner"]
    assert retired
    assert plan.would_delete is False
    sql = plan.deactivation_sql.upper()
    assert "DELETE" not in sql
    assert "SET ACTIVE = FALSE" in sql.replace("active", "ACTIVE")


def test_allowlisted_root_never_proposed_for_disable(master_six_rows, runtime_eight_plus_retired, allowlist):
    plan = build_reconciliation_plan(master_six_rows, runtime_eight_plus_retired, allowlist)
    proposed_ids = {item.actor_id for item in plan.proposed_actor_deactivations}
    assert "viktor" not in proposed_ids
    assert "viktor-test" not in proposed_ids


def test_referral_profile_follows_actor_disable(allowlist):
    runtime = runtime_state_from_rows(
        [
            ("retired-partner", "Retired Partner", True),
        ],
        profiles=[("retired-ref", "retired-partner", True)],
    )
    master_rows = validate_master_rows(parse_partners_ref_tsv(FIXTURES / "master_one_partner.tsv"))
    plan = build_reconciliation_plan(master_rows, runtime, allowlist)
    assert not any(item.actor_id == "retired-partner" for item in plan.proposed_actor_deactivations)

    runtime_extra = runtime_state_from_rows(
        [
            ("retired-partner", "Retired Partner", True),
            ("only-runtime", "Only Runtime", True),
        ],
        profiles=[
            ("retired-ref", "retired-partner", True),
            ("only-ref", "only-runtime", True),
        ],
    )
    plan2 = build_reconciliation_plan(master_rows, runtime_extra, allowlist)
    assert any(item.actor_id == "only-runtime" for item in plan2.proposed_actor_deactivations)
    assert any(item.ref_code == "only-ref" for item in plan2.proposed_profile_deactivations)


def test_idempotent_rerun(allowlist):
    master_rows = validate_master_rows(parse_partners_ref_tsv(FIXTURES / "master_six_partners.tsv"))
    runtime = runtime_state_from_rows(
        [
            ("nnm", "WWC Platform", True),
            ("viktor", "Viktor", True),
            ("viktor-test", "Viktor Test", True),
            ("only-runtime", "Only Runtime", True),
        ],
        profiles=[("only-ref", "only-runtime", True)],
    )
    plan = build_reconciliation_plan(master_rows, runtime, allowlist, mode="apply")
    after = apply_plan_in_memory(master_rows, runtime, plan)
    plan2 = build_reconciliation_plan(master_rows, after, allowlist, mode="apply")
    assert not plan2.proposed_actor_deactivations
    assert not plan2.proposed_profile_deactivations
    actor = after.actors["only-runtime"]
    assert actor.active is False
    profile = next(item for item in after.profiles if item.ref_code == "only-ref")
    assert profile.enabled is False


def test_malformed_master_aborts_before_mutation(allowlist):
    with pytest.raises(MasterSourceError):
        parse_partners_ref_tsv(FIXTURES / "master_malformed.tsv")

    with pytest.raises(MasterSourceError):
        validate_master_rows([])


def test_empty_master_aborts(allowlist):
    with pytest.raises(MasterSourceError):
        validate_master_rows([{"partner_id": "", "display_name": "x"}])


def test_deactivation_sql_only_updates(allowlist):
    actor_proposals, profile_proposals = propose_deactivations(
        runtime_state_from_rows([("ghost", "Ghost", True)], profiles=[("ghost-ref", "ghost", True)]),
        master_ids=["nnm"],
        allowlist=allowlist,
    )
    sql = build_deactivation_sql(actor_proposals, profile_proposals)
    assert "DELETE" not in sql.upper()
    assert "UPDATE lead_actors" in sql
    assert "UPDATE referral_profiles" in sql


def test_master_upsert_sql_matches_partners_contract(master_six_rows):
    sql = build_master_upsert_sql(master_six_rows)
    assert "INSERT INTO lead_actors" in sql
    assert "INSERT INTO referral_profiles" in sql
    assert "'nnm'" in sql
    assert "owner_id = excluded.owner_id" in sql
    assert '"owner_actor_id": "viktor"' in sql


def _load_readonly_module():
    from whieda_partner_runtime_readonly_runtime import (  # noqa: E402
        ConfigConflictError,
        RuntimeReadError,
        assert_readonly_sql_only,
        build_v2_parity_report,
        classify_parity_summary,
        redact_sensitive_text,
        sanitize_public_report,
        validate_apply_conflict,
    )

    return {
        "ConfigConflictError": ConfigConflictError,
        "RuntimeReadError": RuntimeReadError,
        "assert_readonly_sql_only": assert_readonly_sql_only,
        "build_v2_parity_report": build_v2_parity_report,
        "classify_parity_summary": classify_parity_summary,
        "redact_sensitive_text": redact_sensitive_text,
        "sanitize_public_report": sanitize_public_report,
        "validate_apply_conflict": validate_apply_conflict,
    }


def test_runtime_readonly_sql_only():
    ro = _load_readonly_module()
    ro["assert_readonly_sql_only"](["BEGIN READ ONLY", "SELECT 1", "COMMIT"])
    with pytest.raises(ro["RuntimeReadError"]):
        ro["assert_readonly_sql_only"](["UPDATE lead_actors SET active=false"])


def test_apply_with_runtime_dsn_env_refused():
    ro = _load_readonly_module()
    with pytest.raises(ro["ConfigConflictError"]):
        ro["validate_apply_conflict"](apply=True, runtime_dsn_env="WHIEDA_RUNTIME_READONLY_DSN")


def test_dsn_redacted_in_errors_and_reports():
    ro = _load_readonly_module()
    raw = "connect postgresql://admin:hunter2@db.example.com:5432/whieda password=secret"
    redacted = ro["redact_sensitive_text"](raw)
    assert "hunter2" not in redacted
    assert "secret" not in redacted or "[REDACTED]" in redacted
    report = ro["sanitize_public_report"](
        {"abort_reason": raw, "telegram_chat_id": "1147735602", "phone": "+375291234567"}
    )
    blob = json.dumps(report)
    assert "1147735602" not in blob
    assert "+375" not in blob
    assert "hunter2" not in blob


def test_v2_report_classifies_platform_roots_safe(master_six_rows, runtime_eight_plus_retired, allowlist):
    ro = _load_readonly_module()
    plan = build_reconciliation_plan(master_six_rows, runtime_eight_plus_retired, allowlist)
    report = ro["build_v2_parity_report"](
        plan,
        master_rows=master_six_rows,
        allowlist=allowlist,
        master_review_rows=[],
        runtime_source="env:WHIEDA_RUNTIME_READONLY_DSN",
        runtime_profile_count=2,
    )
    assert report["summary"] in {"safe", "review_required"}
    assert "viktor" in report["allowlisted_platform_roots"]
    proposed = {item["actor_id"] for item in report["runtime_only_disable_candidates"]}
    assert "viktor" not in proposed
    assert "retired-partner" in proposed
    assert set(report["in_sync_master_actors"]) == set(plan.master_actor_ids)
    assert report["master_only_needing_upsert"] == []


def test_v2_report_buckets_disjoint_and_exhaustive(allowlist):
    ro = _load_readonly_module()
    master_rows = validate_master_rows(parse_partners_ref_tsv(FIXTURES / "master_six_partners.tsv"))
    runtime = runtime_state_from_rows(
        [
            ("nnm", "WWC Platform", True),
            ("viktor", "Viktor", True),
            ("viktor-test", "Viktor Test", True),
            ("only-runtime", "Only Runtime", True),
            ("retired-partner", "Retired Partner", True),
        ],
        profiles=[("only-ref", "only-runtime", True)],
    )
    missing_master = [row for row in master_rows if row["partner_id"] != "nnm"]
    plan = build_reconciliation_plan(missing_master, runtime, allowlist)
    report = ro["build_v2_parity_report"](
        plan,
        master_rows=missing_master,
        allowlist=allowlist,
        master_review_rows=[],
        runtime_source="fixture",
        runtime_profile_count=1,
    )
    in_sync = set(report["in_sync_master_actors"])
    upsert = set(report["master_only_needing_upsert"])
    disable = {item["actor_id"] for item in report["runtime_only_disable_candidates"]}
    allowlist_ids = set(report["allowlisted_platform_roots"])
    master_ids = set(plan.master_actor_ids)

    assert in_sync & upsert == set()
    assert in_sync | upsert == master_ids
    assert "nnm" not in master_ids
    assert "only-runtime" in disable
    assert "retired-partner" in disable
    assert allowlist_ids & disable == set()
    assert allowlist_ids == {"viktor", "viktor-test"}


def test_duplicate_master_aborts_via_cli_logic():
    duplicate_tsv = FIXTURES / "master_duplicate.tsv"
    duplicate_tsv.write_text(
        "partner_id\tdisplay_name\na\tA\na\tB\n",
        encoding="utf-8",
    )
    with pytest.raises(MasterSourceError, match="duplicate"):
        validate_master_rows(parse_partners_ref_tsv(duplicate_tsv))


def test_missing_runtime_relation_aborts(monkeypatch):
    ro = _load_readonly_module()

    class FakeCursor:
        def __init__(self):
            self._query = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params=None):
            self._query = query

        def fetchone(self):
            if "information_schema" in self._query:
                return None
            return ("a", "A", True, "")

        def fetchall(self):
            return []

    class FakeConn:
        read_only = False

        def transaction(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    class FakePsycopg:
        @staticmethod
        def connect(_dsn):
            return FakeConn()

    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg())
    from whieda_partner_runtime_readonly_runtime import load_runtime_from_readonly_dsn

    with pytest.raises(ro["RuntimeReadError"], match="missing required runtime relation"):
        load_runtime_from_readonly_dsn("postgresql://u:p@127.0.0.1:5432/db", "whieda")


def test_tenant_isolation_in_runtime_read(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.params = None
            self._query = ""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, params=None):
            self.params = params
            self._query = query

        def fetchone(self):
            if "information_schema" in self._query:
                return (1,)
            return None

        def fetchall(self):
            if self.params and self.params[0] != "whieda":
                return []
            if "lead_actors" in self._query:
                return [("viktor", "Viktor", True, "")]
            return [("nnm", "viktor", True, "anonymous")]

    class FakeConn:
        read_only = False

        def transaction(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    class FakePsycopg:
        @staticmethod
        def connect(_dsn):
            return FakeConn()

    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg())
    from whieda_partner_runtime_readonly_runtime import load_runtime_from_readonly_dsn

    state = load_runtime_from_readonly_dsn("postgresql://u:p@127.0.0.1:5432/db", "whieda")
    assert "viktor" in state.actors
    assert all(actor_id != "other-tenant-actor" for actor_id in state.actors)


def test_public_report_has_no_chat_id_or_profile_json():
    ro = _load_readonly_module()
    report = ro["sanitize_public_report"](
        {
            "profiles": [{"public_profile": {"phone": "123"}, "telegram_chat_id": "999"}],
            "actors": [{"telegram_username": "user"}],
        }
    )
    blob = json.dumps(report)
    assert "public_profile" not in blob
    assert "telegram_chat_id" not in blob
    assert "[present]" in blob or "telegram_username" not in blob
