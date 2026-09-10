"""Repo nginx template must keep a single cart facade block. Live duplicates are a lead task."""

from pathlib import Path

CONF = (
    Path(__file__).resolve().parents[3]
    / "backend"
    / "deploy"
    / "core"
    / "wwc.best.nginx-core-routes.conf"
)


def test_repo_template_has_one_cart_sessions_location():
    text = CONF.read_text(encoding="utf-8")
    assert CONF.is_file()
    assert text.count("location ^~ /api/v1/cart-sessions") == 1
    assert text.count("location ^~ /api/v1/cart-snapshots") == 1
    assert "X-Forwarded-Host wwc.best" in text
    assert "sysarchn8n.duckdns.org/whieda-platform/api/v1/cart-sessions" in text
    assert ":5678" not in text


def test_repo_template_lead_alias_is_same_core_upstream():
    text = CONF.read_text(encoding="utf-8")
    assert text.count("location = /api/lead") == 1
    lead = text.split("location = /api/lead", 1)[1].split("location", 1)[0]
    assert "whieda-platform/api/lead" in lead
    assert ":5678" not in lead
    assert "limit_except POST" in lead


def test_repo_template_has_content_access_location():
    text = CONF.read_text(encoding="utf-8")
    assert text.count("location ^~ /api/v1/content-access") == 1
    block = text.split("location ^~ /api/v1/content-access", 1)[1]
    assert "whieda-platform/api/v1/content-access" in block
    assert ":5678" not in block.split("location", 1)[0]


def test_repo_template_has_partner_library_location():
    text = CONF.read_text(encoding="utf-8")
    assert text.count("location ^~ /api/v1/partner-library") == 1
    block = text.split("location ^~ /api/v1/partner-library", 1)[1].split("location", 1)[0]
    assert "whieda-platform/api/v1/partner-library" in block
    assert "limit_except GET" in block
    assert "X-Forwarded-Host wwc.best" in block
    assert ":5678" not in block


def test_repo_template_has_theme_access_location():
    text = CONF.read_text(encoding="utf-8")
    assert text.count("location ^~ /api/v1/theme-access") == 1
    block = text.split("location ^~ /api/v1/theme-access", 1)[1].split("location", 1)[0]
    assert "whieda-platform/api/v1/theme-access" in block
    assert "X-Forwarded-Host wwc.best" in block
    assert "X-WWC-Personal-Host $host" in block
    assert ":5678" not in block


def test_repo_template_snapshots_are_get_only():
    text = CONF.read_text(encoding="utf-8")
    snap = text.split("location ^~ /api/v1/cart-snapshots", 1)[1].split("location", 1)[0]
    sessions = text.split("location ^~ /api/v1/cart-sessions", 1)[1].split("location", 1)[0]
    assert "limit_except GET" in snap
    assert "limit_except GET" not in sessions
