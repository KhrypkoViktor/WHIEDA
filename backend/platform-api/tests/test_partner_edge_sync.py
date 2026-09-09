from __future__ import annotations

import hashlib
import hmac
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "core"
    / "sync_partner_host_map.py"
)
DEPLOY_ROOT = SCRIPT.parent
SPEC = importlib.util.spec_from_file_location("sync_partner_host_map", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def snapshot(*, now: datetime, hosts: list[str] | None = None) -> dict:
    allowed_hosts = hosts or ["active.wwc.best", "grace.wwc.best"]
    return {
        "tenant_id": "whieda",
        "generated_at": now.isoformat(),
        "version": sync.canonical_host_version(allowed_hosts),
        "allowed_hosts": allowed_hosts,
    }


def test_verify_signature_accepts_only_matching_body():
    body = b'{"tenant_id":"whieda"}'
    secret = "edge-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sync.verify_signature(body, f"sha256={signature}", secret)

    with pytest.raises(sync.SnapshotError, match="mismatch"):
        sync.verify_signature(body + b" ", f"sha256={signature}", secret)


def test_snapshot_validation_and_rendering():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    payload = sync.validate_snapshot(snapshot(now=now), expected_tenant="whieda", now=now)
    rendered = sync.render_nginx_map(payload)

    assert "default 0;" in rendered
    assert "active.wwc.best 1;" in rendered
    assert "grace.wwc.best 1;" in rendered
    assert "suspended.wwc.best" not in rendered


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(tenant_id="nsp"), "tenant mismatch"),
        (lambda value: value.update(allowed_hosts=[]), "non-empty"),
        (
            lambda value: value.update(allowed_hosts=["staging.wwc.best"]),
            "reserved hostname",
        ),
        (lambda value: value.update(version="b" * 64), "does not match"),
    ],
)
def test_snapshot_rejects_unsafe_payloads(mutation, message):
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    payload = snapshot(now=now)
    mutation(payload)
    with pytest.raises(sync.SnapshotError, match=message):
        sync.validate_snapshot(payload, expected_tenant="whieda", now=now)


def test_snapshot_rejects_stale_or_future_timestamp():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(sync.SnapshotError, match="stale"):
        sync.validate_snapshot(
            snapshot(now=now - timedelta(seconds=301)),
            expected_tenant="whieda",
            now=now,
        )
    with pytest.raises(sync.SnapshotError, match="future"):
        sync.validate_snapshot(
            snapshot(now=now + timedelta(seconds=31)),
            expected_tenant="whieda",
            now=now,
        )


def test_failed_full_validation_restores_last_known_good(tmp_path, monkeypatch):
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    output = tmp_path / "partner-map.conf"
    state = tmp_path / "state.json"
    previous = b"map $host $wwc_partner_access_allowed { default 0; old.wwc.best 1; }\n"
    output.write_bytes(previous)
    calls = []

    monkeypatch.setattr(sync, "validate_candidate", lambda candidate, nginx: None)

    def fake_run(command):
        calls.append(command)
        if len(calls) == 1:
            raise sync.SnapshotError("full nginx validation failed")

    monkeypatch.setattr(sync, "run_checked", fake_run)

    payload = snapshot(now=now)
    with pytest.raises(sync.SnapshotError, match="validation failed"):
        sync.install_map(
            output=output,
            state_path=state,
            rendered=sync.render_nginx_map(payload).encode(),
            snapshot=payload,
            nginx="nginx",
        )

    assert output.read_bytes() == previous
    assert not state.exists()
    assert len(calls) == 3


def test_failed_first_install_does_not_leave_invalid_map(tmp_path, monkeypatch):
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    output = tmp_path / "partner-map.conf"
    state = tmp_path / "state.json"
    monkeypatch.setattr(sync, "validate_candidate", lambda candidate, nginx: None)
    monkeypatch.setattr(
        sync,
        "run_checked",
        lambda command: (_ for _ in ()).throw(sync.SnapshotError("full test failed")),
    )

    payload = snapshot(now=now)
    with pytest.raises(sync.SnapshotError, match="full test failed"):
        sync.install_map(
            output=output,
            state_path=state,
            rendered=sync.render_nginx_map(payload).encode(),
            snapshot=payload,
            nginx="nginx",
        )

    assert not output.exists()
    assert not state.exists()


def test_nginx_and_systemd_templates_keep_the_edge_contract():
    gate = (DEPLOY_ROOT / "nginx" / "wwc-partner-access-gate.conf").read_text()
    staging = (DEPLOY_ROOT / "nginx" / "wwc-partner-edge-staging.conf.example").read_text()
    timer = (DEPLOY_ROOT / "systemd" / "wwc-partner-edge-sync.timer").read_text()
    service = (DEPLOY_ROOT / "systemd" / "wwc-partner-edge-sync.service").read_text()

    assert "return 302 https://wwc.best$uri;" in gate
    assert "$request_uri" not in gate
    assert "127.0.0.1:8443" in staging
    assert "listen 443" not in staging
    assert "listen [::]:443" not in staging
    assert "OnUnitActiveSec=60s" in timer
    assert "EnvironmentFile=/etc/wwc/partner-edge-sync.env" in service
    assert "PLATFORM_EDGE_SNAPSHOT_SECRET" not in service
