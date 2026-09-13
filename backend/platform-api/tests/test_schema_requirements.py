"""The schema requirement map must keep up with the SQL the code actually runs."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from app.schema_requirements import (
    FEATURE_TABLES,
    OPTIONAL_FEATURE_PACKAGES,
    missing_tables,
    parse_disabled_features,
    required_tables,
)

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
APP_DIR = ROOT / "backend" / "platform-api" / "app"

_TABLE_REF = re.compile(r"\b(?:from|join|into|update|delete\s+from)\s+(?:public\.)?([a-z_][a-z0-9_]*)", re.I)
_CREATE = re.compile(r"create table(?: if not exists)?\s+(?:public\.)?(\w+)", re.I)


def _known_tables() -> set[str]:
    names: set[str] = set()
    for path in SQL_DIR.glob("*.sql"):
        names |= {m.group(1).lower() for m in _CREATE.finditer(path.read_text(encoding="utf-8", errors="ignore"))}
    return names


def _referenced_tables_by_package() -> dict[str, set[str]]:
    known = _known_tables()
    per: dict[str, set[str]] = defaultdict(set)
    for path in APP_DIR.rglob("*.py"):
        parts = path.relative_to(APP_DIR).parts
        package = parts[0][:-3] if parts[0].endswith(".py") else parts[0]
        for match in _TABLE_REF.finditer(path.read_text(encoding="utf-8")):
            name = match.group(1).lower()
            if name in known:
                per[package].add(name)
    return per


def test_every_table_the_code_touches_is_mapped_to_a_feature():
    mapped = set().union(*FEATURE_TABLES.values())
    unmapped = {
        (package, table)
        for package, tables in _referenced_tables_by_package().items()
        for table in tables
        if table not in mapped
    }
    assert not unmapped, f"add these tables to app/schema_requirements.py: {sorted(unmapped)}"


def test_optional_feature_tables_are_only_used_by_their_own_package():
    """A core module must not quietly depend on a table an operator may switch off."""
    per = _referenced_tables_by_package()
    optional_packages = {pkg for pkgs in OPTIONAL_FEATURE_PACKAGES.values() for pkg in pkgs}
    for feature, tables in FEATURE_TABLES.items():
        if feature == "core":
            continue
        owners = OPTIONAL_FEATURE_PACKAGES[feature]
        for package, referenced in per.items():
            if package in optional_packages:
                continue
            leaked = referenced & tables
            assert not leaked, f"core package {package!r} uses {sorted(leaked)} owned by optional feature {feature!r}"
        assert all(per.get(pkg, set()) <= tables | FEATURE_TABLES["core"] for pkg in owners)


def test_disabled_features_drop_their_tables_from_the_requirement():
    everything = required_tables(())
    assert "user_memory_facts" in everything and "lead_actors" in everything
    trimmed = required_tables(parse_disabled_features("pilot, retention,partner_library"))
    assert {"pilot_daily_metrics", "data_export_requests", "partner_library_items"}.isdisjoint(trimmed)
    assert "lead_actors" in trimmed and "user_memory_facts" in trimmed
    with pytest.raises(ValueError):
        parse_disabled_features("telegram")


def test_missing_tables_reports_only_required_gaps():
    existing = required_tables(()) - {"user_memory_facts", "pilot_daily_metrics"}
    assert missing_tables(existing, ()) == ["pilot_daily_metrics", "user_memory_facts"]
    assert missing_tables(existing, {"pilot", "memory"}) == []
