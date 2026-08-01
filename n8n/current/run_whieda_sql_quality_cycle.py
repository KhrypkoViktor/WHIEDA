"""WHIEDA Structure Basic quality runner.

Stage 1 of the 2026-07-25 plan: create a repeatable live baseline without
changing the workflow. Later source-batch handling is intentionally separate
from publishing, so a bad batch cannot silently change production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
EXPORT_ROOT = BASE_DIR.parent / "live-exports" / datetime.now().date().isoformat()
WORKFLOW_ID = "advisor-whieda-phase1"
KEY_NODE_NAMES = (
    "Code: Normalize Payload",
    "Code: Structured Sheet Lookup",
    "Code: Structured Resource Lookup",
    "Code: Validate Dify Response",
    "Telegram: Send Answer",
)
SMOKE_SCRIPTS = (
    ("p0", BASE_DIR / "whieda_live_p0_smoke_2026-07-13.py", "WHIEDA_live_p0_smoke_report.json"),
    ("regression", BASE_DIR / "run_whieda_live_regression_suite_v1_2026-07-15.py", "WHIEDA_live_regression_suite_v1.json"),
)
SMOKE_CASES_TSV_URL = (
    "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/"
    "export?format=tsv&gid=2001023"
)
SOURCE_EXTENSIONS = {".md", ".txt", ".csv"}
SOURCE_BATCH_SIZE = 100
PERSONAL_DATA_PATTERNS = (
    (re.compile(r"(?<!\w)@[a-zA-Z0-9_]{3,}(?!\w)"), "@user"),
    (re.compile(r"(?<!\w)\+?\d[\d\s()\-]{8,}\d(?!\w)"), "[phone]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[email]"),
)
QUESTION_STARTERS = (
    "как ", "что ", "сколько ", "можно ", "нужно ", "подойдет ",
    "есть ли", "почему ", "когда ", "где ", "какой ", "какая ",
    "какие ", "чем ", "помогает ", "расскажи", "покажи", "дай ",
)
MEDICAL_REVIEW_TERMS = (
    "диагноз", "онколог", "рак", "беремен", "кардиостим", "стент", "шунт",
    "инфаркт", "инсульт", "операц", "ребен", "ребён", "гиперто", "давлен",
    "температур", "варикоз", "щитовид", "грыж", "миома", "диабет", "болезн",
)
CONTEXTUAL_RISK_TERMS = (
    "леч", "помочь", "симптом", "бол", "отек", "отёк", "кожа", "глаз", "нос",
    "лицо", "рук", "спина", "шея", "колен", "сустав", "режим", "инструкц",
    "использовать", "пользоваться", "применять", "работать", "процедур", "массаж",
    "вода", "эффект", "результат", "лекар", "прием", "приём", "проблем", "отзыв",
    "здоров", "ребен", "ребён", "родинк", "сып", "зажив", "герпес",
    "псориаз", "ухаж", "подолог", "эндометриоз", "склероз", "подагр",
)
ENTITY_PATTERNS = (
    ("Активатор клеток PRO", ("активатор pro", "активатор-про", "активатор про")),
    ("Активатор клеток", ("активатор", "активатор клеток")),
    ("БЭМ", ("бэм", "биоэнергомассажер", "биоэнергомассажёр", "magic foherb")),
    ("Минисауна Ба-Гуа", ("ба гуа", "ба-гу", "ба гуа", "минисауна", "сауна")),
    ("Вэнтун", ("вентун", "вэнтун")),
    ("Очки", ("очки",)),
    ("Стельки", ("стельки",)),
    ("Палантин", ("палантин",)),
    ("Чай Лювей", ("лювей", "чай")),
    ("Соевый пептид", ("пептид", "пептиды", "соевый пептид")),
)


def load_publisher():
    path = BASE_DIR / "publish_whieda_answer_photo_then_text_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_live", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_workflow(publisher) -> dict:
    session = requests.Session()
    response = session.post(
        f"{publisher.BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": publisher.EMAIL, "password": publisher.PASSWORD},
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    response = session.get(
        f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", payload)


def write_workflow_backup(workflow: dict) -> dict:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workflow_path = EXPORT_ROOT / f"advisor-whieda-phase1_baseline_{stamp}.json"
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    by_name = {node.get("name"): node for node in workflow.get("nodes", [])}
    node_hashes = {
        name: stable_hash(by_name[name].get("parameters", {}))
        for name in KEY_NODE_NAMES
        if name in by_name
    }
    return {"workflow_path": str(workflow_path), "workflow_hash": stable_hash(workflow), "node_hashes": node_hashes}


def run_smoke_suites(suites: tuple[str, ...]) -> list[dict]:
    results = []
    for suite, script, report_name in SMOKE_SCRIPTS:
        if suite not in suites:
            continue
        completed = subprocess.run(
            [sys.executable, str(script)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=1200,
        )
        result = {
            "suite": suite,
            "script": str(script),
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout[-1200:],
            "stderr_tail": completed.stderr[-1200:],
            "report_path": str(EXPORT_ROOT / report_name),
        }
        results.append(result)
        if completed.returncode != 0:
            raise RuntimeError(f"{suite} smoke failed: {completed.stderr[-800:]}")
    return results


def fetch_smoke_plan() -> dict:
    """Read the editable test plan from the Sheet without changing production."""
    response = requests.get(SMOKE_CASES_TSV_URL, timeout=30)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text), delimiter="\t"))
    enabled = [row for row in rows if str(row.get("enabled", "")).strip().lower() in {"true", "1", "yes", "да"}]
    by_priority: dict[str, int] = {}
    by_suite: dict[str, int] = {}
    for row in enabled:
        by_priority[row.get("priority") or "unassigned"] = by_priority.get(row.get("priority") or "unassigned", 0) + 1
        by_suite[row.get("suite") or "unassigned"] = by_suite.get(row.get("suite") or "unassigned", 0) + 1
    return {
        "source": SMOKE_CASES_TSV_URL,
        "total_rows": len(rows),
        "enabled_rows": len(enabled),
        "by_priority": by_priority,
        "by_suite": by_suite,
        "ready": len(enabled) >= 30,
    }


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source_text(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def anonymize(value: str) -> str:
    cleaned = value
    for pattern, replacement in PERSONAL_DATA_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def normalize_question(value: str) -> str:
    value = anonymize(value).casefold().strip()
    value = re.sub(r"[«»\"'`]+", "", value)
    value = re.sub(r"[^\wа-яё$]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def infer_intent(value: str) -> str:
    text = normalize_question(value)
    if any(token in text for token in ("цен", "сколько стоит", "w$", "pv", "первичк", "повторк")):
        return "product_price"
    if any(token in text for token in ("фото", "картинк", "изображен")):
        return "product_photo"
    if any(token in text for token in ("видео", "ролик", "обучени")):
        return "product_video"
    if any(token in text for token in ("сравн", "отлич", "pro", "про версия", "базов")):
        return "product_compare"
    if any(token in text for token in ("противопоказ", "беремен", "онколог", "кардиостим", "стент", "температур", "давлен")):
        return "product_safety"
    if any(token in text for token in ("доход", "бинар", "step", "степ", "кабинет", "партнер", "партнёр", "маркетинг")):
        return "business_faq"
    if any(token in text for token in ("расскажи", "что такое", "что это", "подробнее", "как работает")):
        return "product_card"
    return "unknown"


def infer_entity(value: str) -> str:
    text = normalize_question(value)
    for entity, aliases in ENTITY_PATTERNS:
        if any(normalize_question(alias) in text for alias in aliases):
            return entity
    return ""


def candidate_lines(text: str) -> list[str]:
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not 6 <= len(line) <= 420:
            continue
        lower = line.casefold()
        if "http://" in lower or "https://" in lower or "www." in lower:
            continue
        word_count = len(re.findall(r"[\wа-яё]+", lower, flags=re.IGNORECASE))
        is_question = "?" in line or (lower.startswith(QUESTION_STARTERS) and word_count >= 4)
        if is_question:
            lines.append(anonymize(line))
    return lines


def triage_candidate(question: str, intent_id: str, entity_id: str) -> str:
    normalized = normalize_question(question)
    if len(normalized) < 12 or len(normalized.split()) < 3:
        return "noise_or_fragment"
    if any(term in normalized for term in MEDICAL_REVIEW_TERMS + CONTEXTUAL_RISK_TERMS):
        return "medical_review_required"
    generic_safe = (
        (intent_id == "product_price" and bool(re.search(r"\b(цен[ауые]?|сколько стоит|стоимость)\b", normalized)))
        or (intent_id == "product_photo" and bool(re.search(r"\b(фото|картинк)\b", normalized)))
        or (intent_id == "product_video" and bool(re.search(r"^(есть|где|дай|дайте|покажи|покажите|скиньте|можно).{0,30}\bвидео\b", normalized)))
        or (intent_id == "product_card" and normalized.startswith("что такое"))
        or (intent_id == "product_compare" and normalized.startswith("чем отличается"))
    )
    if generic_safe and entity_id and len(normalized) <= 120:
        return "safe_test_candidate"
    if intent_id == "unknown":
        return "intent_review_required"
    return "review_required"


def process_source_batch(source_batch: Path, cycles: int, rebuild: bool = False) -> dict:
    if not source_batch.is_dir():
        raise SystemExit("--source-batch must point to a folder of .md/.txt/.csv sources")
    state_root = EXPORT_ROOT / "quality-state"
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / "source_registry.json"
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"sources": {}}
    known_sources = previous.get("sources", {})
    files = sorted(path for path in source_batch.rglob("*") if path.is_file() and path.suffix.casefold() in SOURCE_EXTENSIONS)
    registry: dict[str, dict] = {}
    extracted: list[dict] = []
    processed_files = []
    skipped_files = []
    max_files = max(1, cycles) * SOURCE_BATCH_SIZE

    for path in files:
        if len(processed_files) >= max_files:
            break
        digest = source_hash(path)
        source_id = f"SRC-{digest[:12].upper()}"
        relative_path = str(path.relative_to(source_batch))
        previous_row = known_sources.get(relative_path)
        registry[relative_path] = {
            "source_id": source_id,
            "path": str(path),
            "relative_path": relative_path,
            "type": path.suffix.lstrip(".").lower(),
            "hash": digest,
            "status": "processed",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        if not rebuild and previous_row and previous_row.get("hash") == digest:
            skipped_files.append(relative_path)
            registry[relative_path]["status"] = "unchanged"
            continue
        text = read_source_text(path)
        processed_files.append(relative_path)
        for line in candidate_lines(text):
            extracted.append({
                "source_id": source_id,
                "source_path": relative_path,
                "question": line,
                "normalized": normalize_question(line),
                "intent_id": infer_intent(line),
                "entity_id": infer_entity(line),
            })

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in extracted:
        if row["normalized"]:
            grouped[row["normalized"]].append(row)
    candidates = []
    for normalized, rows in grouped.items():
        representative = rows[0]
        examples = []
        for row in rows:
            if row["question"] not in examples and len(examples) < 10:
                examples.append(row["question"])
        candidates.append({
            "candidate_id": f"CQ-{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12].upper()}",
            "canonical_question": representative["question"],
            "normalized": normalized,
            "intent_id": representative["intent_id"],
            "entity_id": representative["entity_id"],
            "frequency": len(rows),
            "examples": examples,
            "source_ids": sorted({row["source_id"] for row in rows}),
            "source_paths": sorted({row["source_path"] for row in rows}),
            "review_bucket": triage_candidate(representative["question"], representative["intent_id"], representative["entity_id"]),
            "publication": "candidate_only",
        })
    candidates.sort(key=lambda row: (-row["frequency"], row["canonical_question"]))

    registry_payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "sources": {**known_sources, **registry}}
    state_path.write_text(json.dumps(registry_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates_path = EXPORT_ROOT / "WHIEDA_question_candidates.json"
    candidates_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = EXPORT_ROOT / "WHIEDA_question_candidates.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=["candidate_id", "canonical_question", "intent_id", "entity_id", "frequency", "examples", "source_ids", "source_paths", "review_bucket", "publication"])
        writer.writeheader()
        for row in candidates:
            writer.writerow({
                "candidate_id": row["candidate_id"],
                "canonical_question": row["canonical_question"],
                "intent_id": row["intent_id"],
                "entity_id": row["entity_id"],
                "frequency": row["frequency"],
                "examples": " | ".join(row["examples"]),
                "source_ids": " | ".join(row["source_ids"]),
                "source_paths": " | ".join(row["source_paths"]),
                "review_bucket": row["review_bucket"],
                "publication": row["publication"],
            })
    return {
        "source_batch": str(source_batch),
        "files_found": len(files),
        "files_processed": len(processed_files),
        "files_unchanged": len(skipped_files),
        "raw_question_hits": len(extracted),
        "unique_candidates": len(candidates),
        "by_intent": dict(Counter(row["intent_id"] for row in candidates)),
        "by_review_bucket": dict(Counter(row["review_bucket"] for row in candidates)),
        "candidate_report": str(candidates_path),
        "candidate_csv": str(csv_path),
        "source_registry": str(state_path),
        "publication": "candidate_only_no_live_content_changed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--run-smoke", action="store_true")
    parser.add_argument("--full-regression", action="store_true")
    parser.add_argument("--source-batch", type=Path)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--apply-safe", action="store_true")
    parser.add_argument("--skip-smoke-plan", action="store_true")
    parser.add_argument("--rebuild-source", action="store_true")
    args = parser.parse_args()

    if args.source_batch is not None and not args.source_batch.exists():
        raise SystemExit(f"Source batch not found: {args.source_batch}")

    publisher = load_publisher()
    workflow = fetch_workflow(publisher)
    baseline = write_workflow_backup(workflow)
    report = {
        "cycle": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "baseline",
        "workflow": baseline,
        "source_batch": str(args.source_batch) if args.source_batch else None,
        "requested_cycles": args.cycles,
        "apply_safe": args.apply_safe,
        "smoke": [],
        "smoke_plan": None,
        "publication": "none",
        "note": "Source extraction writes only auditable candidate artifacts; approved content is never changed by this runner.",
    }
    if not args.skip_smoke_plan:
        report["smoke_plan"] = fetch_smoke_plan()
    if args.full_regression:
        args.run_smoke = True
    if args.run_smoke:
        requested_suites = ("p0", "regression") if args.full_regression else ("p0",)
        report["smoke"] = run_smoke_suites(requested_suites)
        report["smoke_mode"] = "full" if args.full_regression else "fast_p0"
    if args.source_batch is not None:
        report["source_processing"] = process_source_batch(args.source_batch, args.cycles, args.rebuild_source)
        if args.apply_safe:
            report["publication"] = "candidate_artifacts_updated_only"

    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = EXPORT_ROOT / "WHIEDA_sql_quality_cycle_baseline.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "workflow": baseline, "smoke_suites": len(report["smoke"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
