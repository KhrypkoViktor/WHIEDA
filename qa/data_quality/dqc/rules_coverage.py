"""Quality rule registry coverage verification."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


CHECK_IN_ISSUE = re.compile(r'_issue\(\s*"[^"]+"\s*,\s*"([a-z_]+)"')
CHECK_IN_DICT = re.compile(r'"check"\s*:\s*"([a-z_]+)"')


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def active_rules(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [rule for rule in registry.get("rules", []) if rule.get("status") == "active"]


def extract_code_checks(*paths: Path) -> set[str]:
    found: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        found.update(CHECK_IN_ISSUE.findall(text))
        found.update(CHECK_IN_DICT.findall(text))
    return found


def parse_pytest_rule_map(test_path: Path) -> dict[str, set[str]]:
    """Map rule_id -> pytest test function names that assert it."""
    source = test_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    mapping: dict[str, set[str]] = {}

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        if node.name != "test_quality_checks_detect_issues":
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute) or dec.func.attr != "parametrize":
                continue
            if len(dec.args) < 2:
                continue
            arg_node = dec.args[1]
            if isinstance(arg_node, ast.List):
                for elt in arg_node.elts:
                    if isinstance(elt, ast.Tuple) and len(elt.elts) >= 2:
                        rule = _const_str(elt.elts[1])
                        if rule:
                            mapping.setdefault(rule, set()).add(node.name)
    return mapping


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def verify_coverage(
    *,
    registry_path: Path,
    dqc_root: Path,
    fixtures_root: Path,
    test_path: Path,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    rules = active_rules(registry)
    registry_ids = {rule["rule_id"] for rule in rules}
    all_rule_ids = {rule["rule_id"] for rule in registry.get("rules", [])}

    code_checks = extract_code_checks(dqc_root / "dqc" / "schema.py", dqc_root / "dqc" / "checks.py")
    pytest_map = parse_pytest_rule_map(test_path)

    missing_registry: list[str] = []
    for check in sorted(code_checks):
        if check not in all_rule_ids:
            missing_registry.append(check)

    missing_fixture: list[str] = []
    missing_pytest: list[str] = []
    covered_fixture = 0
    covered_pytest = 0

    for rule in rules:
        rule_id = rule["rule_id"]
        scenario = rule.get("fixture_scenario", "")
        scenario_dir = fixtures_root / "scenarios" / scenario if scenario else None
        has_fixture = bool(scenario_dir and scenario_dir.is_dir() and any(scenario_dir.iterdir()))
        has_pytest = rule_id in pytest_map

        if has_fixture:
            covered_fixture += 1
        else:
            missing_fixture.append(rule_id)

        if has_pytest:
            covered_pytest += 1
        else:
            missing_pytest.append(rule_id)

    unknown_fixture_rules: list[str] = []
    for rule in registry.get("rules", []):
        rule_id = rule.get("rule_id", "")
        if rule_id and rule_id not in all_rule_ids:
            unknown_fixture_rules.append(rule_id)

    # fixture scenarios must reference known rule ids (already in registry by construction)
    invalid_fixture_refs = [r["rule_id"] for r in registry.get("rules", []) if r.get("rule_id") not in all_rule_ids]

    problems = missing_registry + missing_fixture + missing_pytest + invalid_fixture_refs
    status = "PASS" if not problems else "FAIL"

    return {
        "status": status,
        "active_rules": len(rules),
        "covered_fixture": covered_fixture,
        "covered_pytest": covered_pytest,
        "missing_registry": missing_registry,
        "missing_fixture": missing_fixture,
        "missing_pytest": missing_pytest,
        "invalid_fixture_refs": invalid_fixture_refs,
        "code_checks": sorted(code_checks),
    }


def format_report(result: dict[str, Any]) -> str:
    lines = [
        f"Rules coverage status: {result['status']}",
        f"Active rules: {result['active_rules']}",
        f"Covered by fixture: {result['covered_fixture']}",
        f"Covered by pytest: {result['covered_pytest']}",
    ]
    if result["missing_registry"]:
        lines.append(f"Missing registry entries for code checks: {', '.join(result['missing_registry'])}")
    if result["missing_fixture"]:
        lines.append(f"Active rules without fixture: {', '.join(result['missing_fixture'])}")
    if result["missing_pytest"]:
        lines.append(f"Active rules without pytest: {', '.join(result['missing_pytest'])}")
    if result["invalid_fixture_refs"]:
        lines.append(f"Unknown rule refs: {', '.join(result['invalid_fixture_refs'])}")
    if result["status"] == "PASS":
        lines.append("All active rules are registered, fixtured, and tested.")
    return "\n".join(lines)
