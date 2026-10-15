"""Адрес сайта партнёра (15.10.2026): мат и оскорбления не проходят, имена — проходят."""

from app.site_requests.service import subdomain_offensive
from app.subscriptions.service import resolve_partner_hostname


def test_offensive_addresses_are_refused_and_real_names_pass():
    for bad in ("pizda", "pi3da", "huy", "olga-suka", "blyad", "zhopa", "fuck-off", "nazi", "shit"):
        assert subdomain_offensive(bad), bad
    for name in ("lebedevich", "nash", "chuikov", "lebanova", "nazira", "shitova", "sukharev", "kozel", "elena59", "zubkovaludmila"):
        assert not subdomain_offensive(name), name


def test_shown_address_is_the_chosen_subdomain_not_the_code():
    assert resolve_partner_hostname("lebedevich", {"subdomain": "nash"}) == "nash.wwc.best"
    assert resolve_partner_hostname("onlineelena", {}) == "elena.wwc.best"
    assert resolve_partner_hostname("makarova", None) == "makarova.wwc.best"
