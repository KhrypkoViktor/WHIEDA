"""User-facing bot copy uses the agreed names: WWC$, never «баллы», never bare W$."""

from __future__ import annotations

import re
from pathlib import Path

from app.telegram.money import format_minor, money, wwc, wwc_signed

APP = Path(__file__).resolve().parents[1] / "app"
USER_FACING = ("telegram", "subscriptions", "referral_bonus", "renewal_requests", "site_requests", "support")
# String literals that contain Cyrillic: that is what a person can read.
_RU_LITERAL = re.compile(r"""(?:"|')((?:[^"'\n\\]|\\.)*?[А-Яа-яЁё](?:[^"'\n\\]|\\.)*?)(?:"|')""")


def _literals():
    for name in USER_FACING:
        for path in (APP / name).rglob("*.py"):
            for match in _RU_LITERAL.finditer(path.read_text(encoding="utf-8")):
                yield path.name, match.group(1)


def test_money_formatter_speaks_wwc():
    assert format_minor(3000) == "30"
    assert format_minor(1250) == "12,50"
    assert format_minor(123456) == "1 234,56"
    assert wwc(600) == "6 WWC$"
    assert wwc_signed(600) == "+6 WWC$"
    assert wwc_signed(-600) == "−6 WWC$"
    assert money(300000, "RUB") == "3 000 ₽"
    assert money(3000, "WUSD") == "30 WWC$"


# Кабинет партнёра /me/ (02.10.2026): тексты бота (карточка владельцу, ответы
# партнёру, кабинет) и подписи сайта — те же слова про деньги и без обещаний.
_CABINET_COPY = (APP / "cabinet", APP / "telegram" / "cabinet_profile.py")
_PROMISE = re.compile(r"гарант|заработ|доход|разбогате|пассивн|вылеч|излеч|исцел|здоровь", re.I)


def _cabinet_literals():
    for root in _CABINET_COPY:
        for path in ([root] if root.is_file() else sorted(root.rglob("*.py"))):
            for match in _RU_LITERAL.finditer(path.read_text(encoding="utf-8")):
                yield path.name, match.group(1)


def test_cabinet_copy_speaks_wwc_and_promises_nothing():
    from app.telegram.referral_bonus import cabinet_text

    literals = list(_cabinet_literals())
    assert literals, "cabinet copy not found"
    texts = literals + [("referral_bonus.py", cabinet_text(None, balance_minor=0, link="https://t.me/x"))]
    offenders = [
        (f, s) for f, s in texts
        if re.search(r"\bбалл", s, re.I) or re.search(r"(?<![A-Z])W\$", s) or _PROMISE.search(s)
    ]
    assert not offenders, offenders


def test_no_points_wording_anywhere_a_person_reads():
    offenders = [(f, s) for f, s in _literals() if re.search(r"\bбалл", s, re.I)]
    assert not offenders, offenders


def test_no_bare_w_dollar_in_copy():
    offenders = [(f, s) for f, s in _literals() if re.search(r"(?<![A-Z])W\$", s)]
    assert not offenders, offenders


# WWC CRM (02.10.2026): the bot names the product «WWC CRM», not «Ежедневник»;
# the same money words apply to its texts.
def _crm_literals():
    for path in (APP / "crm").rglob("*.py"):
        for match in _RU_LITERAL.finditer(path.read_text(encoding="utf-8")):
            yield path.name, match.group(1)


def test_crm_copy_has_no_points_and_no_bare_w_dollar():
    offenders = [
        (f, s) for f, s in _crm_literals() if re.search(r"\bбалл", s, re.I) or re.search(r"(?<![A-Z])W\$", s)
    ]
    assert not offenders, offenders


def test_crm_is_called_wwc_crm_in_the_bot():
    from app.crm import bot, messages

    named = [bot.CRM_BUTTON_LABEL, bot.OPEN_BUTTON_LABEL, bot.OPEN_TEXT, messages.DIGEST_TITLE]
    assert all("WWC CRM" in text for text in named), named
    shown = [*named, *bot.LOCK_TEXT.values(), messages.OPEN_TODAY_LABEL, messages.OPEN_CARD_LABEL,
             *messages.GROUP_HEADERS.values()]
    assert not any("ежедневник" in text.lower() for text in shown), shown
    # The old word still opens it.
    assert bot.is_crm_text("ежедневник") and bot.is_crm_text("WWC CRM")


# Академия v2 (02.10.2026, решение владельца): слово «полка» убрано — везде «Академия»,
# автор = «Автор Академии». Проверяем и пакет academy: его ошибки видит человек.
ACADEMY_FACING = USER_FACING + ("academy",)


def _academy_literals():
    for name in ACADEMY_FACING:
        for path in (APP / name).rglob("*.py"):
            for match in _RU_LITERAL.finditer(path.read_text(encoding="utf-8")):
                yield path.name, match.group(1)


def test_no_shelf_wording_anywhere_a_person_reads():
    offenders = [(f, s) for f, s in _academy_literals() if re.search(r"\bполк[аиуеоы]", s, re.I)]
    assert not offenders, offenders


# Мастерская WWC (03.10.2026): тексты бота, витрины и каталога (seed V22) — те же слова
# про деньги, без обещаний результата и без слова «терапия» (владелец: «Снять блок»).
_SHOP_COPY = (APP / "shop", APP / "telegram" / "shop.py")
_SHOP_SEED = APP.parents[2] / "postgres" / "sql" / "platform_shop_v22.sql"
_SQL_RU_LITERAL = re.compile(r"'((?:[^'\n]|'')*?[А-Яа-яЁё](?:[^'\n]|'')*?)'")


def _shop_literals():
    for root in _SHOP_COPY:
        for path in ([root] if root.is_file() else sorted(root.rglob("*.py"))):
            for match in _RU_LITERAL.finditer(path.read_text(encoding="utf-8")):
                yield path.name, match.group(1)
    seed = "\n".join(
        line for line in _SHOP_SEED.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--")
    )
    for match in _SQL_RU_LITERAL.finditer(seed):
        yield _SHOP_SEED.name, match.group(1)


def test_shop_copy_speaks_wwc_promises_nothing_and_never_says_therapy():
    from app.telegram.shop import card_text

    literals = list(_shop_literals())
    assert {f for f, _ in literals} >= {"service.py", "shop.py", "platform_shop_v22.sql"}, "shop copy not found"
    assert any("Снять блок" in s for _, s in literals)
    card = card_text({
        "code": "snyat-blok", "kind": "service", "title": "Сессия «Снять блок»", "subtitle": None,
        "description_md": "", "description_html": "", "price_wusd_minor": 10000, "price_rub_minor": 1000000,
    })
    offenders = [
        (f, s) for f, s in literals + [("shop.py", card)]
        if re.search(r"\bбалл", s, re.I) or re.search(r"(?<![A-Z])W\$", s) or _PROMISE.search(s) or re.search(r"терапи", s, re.I)
    ]
    assert not offenders, offenders
