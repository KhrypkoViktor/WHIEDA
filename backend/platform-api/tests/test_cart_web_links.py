"""Calculator / snapshot URL contract. No SKU, owner, or prices in the URL."""

from app.cart.web_links import (
    CALCULATOR_WEB_URL,
    calculator_web_url,
    canonical_calculator_path,
    parse_cart_launch,
    snapshot_share_path,
)


def test_plain_calculator_url_is_calc_mode():
    assert CALCULATOR_WEB_URL == "https://wwc.best/price/?calc=1"
    assert calculator_web_url() == CALCULATOR_WEB_URL


def test_telegram_can_open_existing_cart_session():
    url = calculator_web_url(cart_session_id="KGYN155PJkQCdjCuwOZw2Afj")
    assert url.startswith("https://wwc.best/price/?calc=1")
    parsed = parse_cart_launch(url)
    assert parsed["cart_session_id"] == "KGYN155PJkQCdjCuwOZw2Afj"
    assert parsed["snapshot_token"] is None
    assert "M015" not in url


def test_share_path_is_opaque_snapshot_not_sku_list():
    path = snapshot_share_path("snapTokenNoSku")
    assert path == "/price/?snap=snapTokenNoSku"
    parsed = parse_cart_launch("https://wwc.best" + path)
    assert parsed["snapshot_token"] == "snapTokenNoSku"
    assert parsed["cart_session_id"] is None


def test_cart_param_wins_over_snap_when_both_present():
    parsed = parse_cart_launch("https://wwc.best/price/?calc=1&snap=s1&cart=c1")
    assert parsed["cart_session_id"] == "c1"
    assert parsed["snapshot_token"] is None


def test_canonical_path_drops_launch_state():
    assert canonical_calculator_path() == "/price/?calc=1"


def test_blank_cart_id_stays_plain_calculator():
    assert calculator_web_url(cart_session_id="") == CALCULATOR_WEB_URL
    parsed = parse_cart_launch("https://wwc.best/price/?calc=1")
    assert parsed == {"cart_session_id": None, "snapshot_token": None}


def test_cart_id_roundtrip_encodes_special_characters():
    url = calculator_web_url(cart_session_id="a+b/c")
    assert "M015" not in url
    parsed = parse_cart_launch(url)
    assert parsed["cart_session_id"] == "a+b/c"
    assert "sku" not in url.lower()


def test_sku_query_is_ignored_for_launch():
    parsed = parse_cart_launch("https://wwc.best/price/?calc=1&sku=M015-00")
    assert parsed == {"cart_session_id": None, "snapshot_token": None}


def test_snap_launch_ignores_sku_query():
    parsed = parse_cart_launch("https://wwc.best/price/?snap=tok&sku=M015-00&owner=x")
    assert parsed["snapshot_token"] == "tok"
    assert parsed["cart_session_id"] is None