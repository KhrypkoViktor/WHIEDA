"""Human language rails corpus — load, validate, metrics."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "human_language_rails"
CORPUS_PATH = PKG / "whieda_human_language_rails_v1.jsonl"
FLOWS_PATH = PKG / "flows_v1.jsonl"
PENDING_FIXTURE = PKG / "fixtures" / "pending_assertions.jsonl"

RAILS = ("direct_answer", "product_choices", "task_selection", "universal_menu")

ACCEPTANCE_STATUSES = ("accepted", "pending_surface", "pending_policy")

RAIL_MINIMUMS = {
    "direct_answer": 55,
    "product_choices": 25,
    "task_selection": 30,
    "universal_menu": 40,
}

FORBIDDEN_FRAGMENTS = (
    "не знаю",
    "нет в базе",
    "не смог обработать",
    "передам на проверку",
    "needs human review",
    "traceback",
)

UNIVERSAL_MENU_MUST_CONTAIN_ALL = (
    "Я лучше всего помогаю с товарами WHIEDA",
    "Выберите направление",
)

MALFORMED_PATTERNS = (
    re.compile(r"[a-z]{3,}[ьъы]{1,3}[a-z]{2,}", re.I),
    re.compile(r"[qwertyuiop\[\]]{4,}", re.I),
    re.compile(r"^(цена|фото|видео|сертификат|сравни|pro|паста|пояс)$", re.I),
    re.compile(r"^(прив|здар|хай|че|а что|можешь)", re.I),
)

ASSERTION_REQUIRED = (
    "case_id",
    "priority",
    "flow_id",
    "turn_index",
    "turn_role",
    "user_text",
    "context_before",
    "expected_rail",
    "acceptance_status",
    "must_not_contain",
    "source",
    "rationale",
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def load_flows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return load_cases(path)


def accepted_assertions(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        c
        for c in cases
        if c.get("turn_role") == "assertion" and c.get("acceptance_status") == "accepted"
    ]


def normalize_text(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    return re.sub(r"\s+", " ", lowered)


def context_key(context_before: list[Any]) -> str:
    return json.dumps(context_before or [], ensure_ascii=False, sort_keys=True)


def dedupe_key(case: dict[str, Any]) -> tuple[str, str, str]:
    if case.get("turn_role") != "assertion":
        return ("setup", case.get("case_id", ""), "")
    return (
        normalize_text(str(case.get("user_text") or "")),
        context_key(case.get("context_before") or []),
        str(case.get("expected_rail") or ""),
    )


def _source_exists(ref: str) -> bool:
    ref = str(ref or "").strip()
    if not ref:
        return False
    path_part = ref.split(":", 1)[0]
    candidate = ROOT / path_part.replace("\\", "/")
    if candidate.is_file() or candidate.is_dir():
        return True
    parent = candidate.parent
    return parent.is_file() or parent.is_dir()


def validate_source(source: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    kind = str(source.get("kind") or "").strip()
    ref = str(source.get("ref") or "").strip()
    if not kind:
        errors.append("source.kind missing")
    if not ref:
        errors.append("source.ref missing")
    elif not _source_exists(ref):
        errors.append(f"source.ref not found: {ref}")
    return errors


def is_malformed(text: str) -> bool:
    raw = str(text or "").strip()
    norm = normalize_text(raw)
    if len(norm) <= 3:
        return True
    short_messy = {
        "цена", "фото", "видео", "сертификат", "подробнее", "pro", "пояс", "пasta",
        "хай", "можешь?", "привт", "ку", "ok", "ок", "нет", "ну", "э", "хм",
        "help", "hi", "hello", "...", "123", "??", "ййй", "qwerty", "ghbdtn",
        "a?", "цена?", "фотка", "видос", "сравни",
    }
    if norm in short_messy:
        return True
    for pattern in MALFORMED_PATTERNS:
        if pattern.search(raw):
            return True
    if any(ch in raw for ch in "qwertyuiop[]") and any("\u0400" <= ch <= "\u04FF" for ch in raw):
        return True
    if re.search(
        r"(атив|активatr|спирулин[a-z]|здрасьte|можеь|моешь|приве|привт|поис|шо |чё )",
        raw,
        re.I,
    ):
        return True
    return False


def validate_flow_metadata(cases: list[dict[str, Any]], flows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    corpus_flow_ids = {str(c.get("flow_id") or "") for c in cases if c.get("flow_id")}
    meta_flow_ids = {str(f.get("flow_id") or "") for f in flows if f.get("flow_id")}
    orphans = meta_flow_ids - corpus_flow_ids
    missing = corpus_flow_ids - meta_flow_ids
    if orphans:
        errors.append(f"orphan flow metadata rows: {len(orphans)} e.g. {sorted(orphans)[:3]}")
    if missing:
        errors.append(f"corpus flows missing metadata: {len(missing)} e.g. {sorted(missing)[:3]}")
    if len(meta_flow_ids) != len(corpus_flow_ids):
        errors.append(
            f"flow count mismatch: corpus={len(corpus_flow_ids)} metadata={len(meta_flow_ids)}"
        )
    flow_turn_counts = Counter(str(c.get("flow_id") or "") for c in cases)
    for flow in flows:
        fid = str(flow.get("flow_id") or "")
        expected = flow_turn_counts.get(fid, 0)
        declared = int(flow.get("turn_count") or 0)
        if expected and declared != expected:
            errors.append(f"{fid}: metadata turn_count={declared} corpus={expected}")
    return errors


def validate_cases(
    cases: list[dict[str, Any]],
    *,
    flows: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    assertions = [c for c in cases if c.get("turn_role") == "assertion"]
    accepted = accepted_assertions(cases)

    if len(assertions) < 150:
        errors.append(f"expected >=150 assertion turns, got {len(assertions)}")

    by_rail_accepted = Counter(str(c.get("expected_rail") or "") for c in accepted)
    for rail, minimum in RAIL_MINIMUMS.items():
        if by_rail_accepted.get(rail, 0) < minimum:
            errors.append(
                f"accepted rail {rail}: need >={minimum}, got {by_rail_accepted.get(rail, 0)}"
            )

    flow_ids = {str(c.get("flow_id") or "") for c in cases if c.get("flow_id")}
    multi_flow_ids = {
        fid for fid in flow_ids if sum(1 for c in cases if c.get("flow_id") == fid) >= 2
    }
    if len(multi_flow_ids) < 35:
        errors.append(f"expected >=35 multi-turn flows, got {len(multi_flow_ids)}")

    malformed = sum(1 for c in accepted if is_malformed(str(c.get("user_text") or "")))
    if malformed < 45:
        errors.append(f"expected >=45 malformed accepted assertions, got {malformed}")

    seen: set[tuple[str, str, str]] = set()
    for case in assertions:
        key = dedupe_key(case)
        if key in seen:
            errors.append(f"duplicate assertion tuple: {case.get('case_id')} {key[0][:40]}")
        seen.add(key)

        for field in ASSERTION_REQUIRED:
            if field not in case:
                errors.append(f"{case.get('case_id')}: missing {field}")

        status = str(case.get("acceptance_status") or "")
        if status not in ACCEPTANCE_STATUSES:
            errors.append(f"{case.get('case_id')}: invalid acceptance_status {status!r}")

        rail = str(case.get("expected_rail") or "")
        if rail not in RAILS:
            errors.append(f"{case.get('case_id')}: invalid rail {rail!r}")

        must_not = [str(x).lower() for x in (case.get("must_not_contain") or [])]
        for frag in FORBIDDEN_FRAGMENTS:
            if frag == "traceback":
                if not any("traceback" in x for x in must_not):
                    errors.append(f"{case.get('case_id')}: must_not_contain missing Traceback guard")
            elif frag not in must_not:
                errors.append(f"{case.get('case_id')}: must_not_contain missing {frag!r}")

        if status == "accepted" and rail == "universal_menu":
            must_all = [str(m) for m in (case.get("must_contain_all") or [])]
            for marker in UNIVERSAL_MENU_MUST_CONTAIN_ALL:
                if marker not in must_all:
                    errors.append(f"{case.get('case_id')}: universal_menu missing must_contain_all {marker!r}")

        if status == "pending_surface" and rail == "universal_menu":
            if case.get("must_contain_all"):
                errors.append(f"{case.get('case_id')}: pending_surface must not use must_contain_all")

        src_errors = validate_source(case.get("source") or {})
        errors.extend(f"{case.get('case_id')}: {e}" for e in src_errors)

    if flows is not None:
        errors.extend(validate_flow_metadata(cases, flows))

    return errors


def corpus_stats(cases: list[dict[str, Any]], flows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    assertions = [c for c in cases if c.get("turn_role") == "assertion"]
    accepted = accepted_assertions(cases)
    by_status = Counter(str(c.get("acceptance_status") or "") for c in assertions)
    by_rail_accepted = Counter(str(c.get("expected_rail") or "") for c in accepted)
    by_rail_all = Counter(str(c.get("expected_rail") or "") for c in assertions)
    by_source = Counter(str((c.get("source") or {}).get("kind") or "") for c in accepted)
    flow_ids = {str(c.get("flow_id") or "") for c in cases if c.get("flow_id")}
    multi_flows = [fid for fid in flow_ids if sum(1 for c in cases if c.get("flow_id") == fid) >= 2]
    vague_to_useful = sum(
        1
        for fid in multi_flows
        if any(
            c.get("turn_role") == "assertion"
            and c.get("acceptance_status") == "pending_surface"
            and c.get("flow_id") == fid
            for c in cases
        )
        and any(
            c.get("turn_role") == "assertion"
            and c.get("acceptance_status") == "accepted"
            and c.get("flow_id") == fid
            for c in cases
        )
    )
    context_followups = sum(
        1
        for c in accepted
        if c.get("context_before")
        and normalize_text(str(c.get("user_text") or ""))
        in {"цена", "фото", "видео", "сертификат", "сравни", "убери", "добавь"}
    )
    stats: dict[str, Any] = {
        "assertions_total": len(assertions),
        "assertions_accepted": len(accepted),
        "assertions_pending_surface": by_status.get("pending_surface", 0),
        "assertions_pending_policy": by_status.get("pending_policy", 0),
        "setup_turns": sum(1 for c in cases if c.get("turn_role") == "setup"),
        "flows_total": len(flow_ids),
        "multi_turn_flows": len(multi_flows),
        "by_rail_accepted": dict(by_rail_accepted),
        "by_rail_all": dict(by_rail_all),
        "by_source_kind_accepted": dict(by_source),
        "malformed_accepted": sum(1 for c in accepted if is_malformed(str(c.get("user_text") or ""))),
        "vague_to_useful_flows": vague_to_useful,
        "context_media_followups": context_followups,
        "universal_menu_must_contain_all": list(UNIVERSAL_MENU_MUST_CONTAIN_ALL),
    }
    if flows is not None:
        stats["flows_metadata_rows"] = len(flows)
        stats["flows_reconciled"] = len(flow_ids) == len({f.get("flow_id") for f in flows})
    return stats
