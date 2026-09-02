# -*- coding: utf-8 -*-
"""Release-layer tests for the hardened ref-theme staging deploy script."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ref_theme_slice.py"


def load_module():
    spec = importlib.util.spec_from_file_location("deploy_ref_theme_slice", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_target_is_refused():
    module = load_module()
    with pytest.raises(SystemExit, match="production deploys are disabled"):
        module.resolve_target("production")


def test_staging_target_resolves_with_health_url():
    module = load_module()
    target = module.resolve_target("staging")
    assert target["health_url"] == "http://127.0.0.1:8081/health/ready"
    assert target["remote"].startswith("/opt/whieda-platform-staging")


def test_build_archive_from_head_contains_all_slice_files():
    module = load_module()
    head = __import__("subprocess").run(
        ["git", "-C", str(module.REPO), "rev-parse", "HEAD"], capture_output=True, check=True
    ).stdout.decode().strip()
    archive, sha = module.build_archive(head)
    assert len(sha) == 64
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive), mode="w:gz") as _:  # sanity: readable
        pass
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert names == set(module.FILES)


def test_build_archive_refuses_unknown_commit():
    module = load_module()
    with pytest.raises(RuntimeError, match="git archive failed"):
        module.build_archive("0" * 40)


def test_pinned_host_key_loads_with_fingerprint():
    module = load_module()
    key, fingerprint = module.load_pinned_host_key()
    assert fingerprint.startswith("SHA256:")
    assert key.get_name() == "ssh-ed25519"
    pin = json.loads(module.KEYS_FILE.read_text(encoding="utf-8"))
    assert fingerprint == pin["fingerprint"]


def test_flag_constants_match_contract():
    module = load_module()
    assert module.FLAG_NAME == "THEME_TEMPORARY_FREE_FOR_VERIFIED_TELEGRAM_USERS"
    assert module.FLAG_LINE == module.FLAG_NAME + "=true"
