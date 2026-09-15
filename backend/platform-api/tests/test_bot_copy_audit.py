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


def test_no_points_wording_anywhere_a_person_reads():
    offenders = [(f, s) for f, s in _literals() if re.search(r"\bбалл", s, re.I)]
    assert not offenders, offenders


def test_no_bare_w_dollar_in_copy():
    offenders = [(f, s) for f, s in _literals() if re.search(r"(?<![A-Z])W\$", s)]
    assert not offenders, offenders
