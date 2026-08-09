"""Build WHIEDA asset manifest CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from inventory.constants import (
    BLOCKED_DISTILLATE_LAYERS,
    MANIFEST_COLUMNS,
    OBSIDIAN_RAG,
    QA_ROOT,
    RAG_ROOT,
    REPO_ROOT,
    REVIEW_REQUIRED_LAYERS,
    SQL_CORPUS,
)
from inventory.distillate_layers import DISTILLATE_BY_FILENAME, DISTILLATE_LAYERS
from inventory.readers import count_text_lines, read_jsonl_info, read_tsv_info, rel_path

RAW_TESTIMONIALS_PATH = RAG_ROOT / "RAW диалоги с врачами" / "WHIEDA 🗣️ Отзывы.txt"


@dataclass
class ManifestRow:
    asset_id: str
    layer: str
    title: str
    path: str
    format: str
    records_or_lines: str
    source_kind: str
    publication_state: str
    review_state: str
    provenance_state: str
    user_visible: str
    notes: str

    def as_dict(self) -> dict[str, str]:
        return {col: getattr(self, col) for col in MANIFEST_COLUMNS}


def _review_state_for_tsv(filename: str) -> str:
    meta = DISTILLATE_BY_FILENAME.get(filename)
    if meta:
        return meta.review_flag
    if filename in BLOCKED_DISTILLATE_LAYERS:
        return "blocked_raw"
    if filename in REVIEW_REQUIRED_LAYERS:
        return "review_required"
    return "ok"


def _publication_for_distillate(filename: str) -> str:
    if filename in BLOCKED_DISTILLATE_LAYERS:
        return "local_only"
    return "local_only"


def build_manifest_rows() -> list[ManifestRow]:
    rows: list[ManifestRow] = []

    if SQL_CORPUS.is_dir():
        for tsv_path in sorted(SQL_CORPUS.glob("*.tsv")):
            if tsv_path.parent.name.startswith("_"):
                continue
            meta = DISTILLATE_BY_FILENAME.get(tsv_path.name)
            info = read_tsv_info(tsv_path)
            rows.append(
                ManifestRow(
                    asset_id=meta.asset_id if meta else f"DIST-{tsv_path.stem}",
                    layer=meta.layer if meta else "distillate",
                    title=meta.title if meta else tsv_path.stem,
                    path=rel_path(tsv_path, REPO_ROOT),
                    format="tsv",
                    records_or_lines=str(info.data_rows),
                    source_kind="distillate_corpus",
                    publication_state=_publication_for_distillate(tsv_path.name),
                    review_state=_review_state_for_tsv(tsv_path.name),
                    provenance_state="derived_from_raw",
                    user_visible="no",
                    notes=(
                        f"header_cols={len(info.header)}; "
                        f"target={meta.target_destination if meta else 'review'}"
                    ),
                )
            )

    # RAW testimonials
    if RAW_TESTIMONIALS_PATH.exists():
        lines = count_text_lines(RAW_TESTIMONIALS_PATH)
        rows.append(
            ManifestRow(
                asset_id="RAW-TESTIMONIALS-TXT",
                layer="raw",
                title="RAW testimonials export",
                path=rel_path(RAW_TESTIMONIALS_PATH, REPO_ROOT),
                format="txt",
                records_or_lines=str(lines),
                source_kind="telegram_export",
                publication_state="raw_only",
                review_state="review_required",
                provenance_state="raw_unverified",
                user_visible="no",
                notes="Not medical evidence; do not auto-publish",
            )
        )

    raw_dir = RAG_ROOT / "RAW диалоги с врачами"
    if raw_dir.is_dir():
        msg_files = [p for p in raw_dir.glob("Copy of _Messages*.txt") if p.is_file()]
        if msg_files:
            total_lines = sum(count_text_lines(p) for p in msg_files)
            rows.append(
                ManifestRow(
                    asset_id="RAW-DIALOGUES-MESSAGES",
                    layer="raw",
                    title="RAW doctor/partner dialogues",
                    path=rel_path(raw_dir, REPO_ROOT),
                    format="txt_bundle",
                    records_or_lines=f"{len(msg_files)} files / ~{total_lines} lines",
                    source_kind="telegram_export",
                    publication_state="raw_only",
                    review_state="review_required",
                    provenance_state="raw_unverified",
                    user_visible="no",
                    notes="29 message exports; distillate covers partial subset",
                )
            )

    deep_corpus = RAG_ROOT / "deep-corpus"
    if deep_corpus.is_dir():
        docs = [p for p in deep_corpus.glob("*.md") if p.name.lower() != "readme.md"]
        for doc in sorted(docs):
            lines = count_text_lines(doc)
            rows.append(
                ManifestRow(
                    asset_id=f"DEEP-{doc.stem[:24].upper().replace(' ', '-')}",
                    layer="deep_rag",
                    title=doc.stem,
                    path=rel_path(doc, REPO_ROOT),
                    format="markdown",
                    records_or_lines=str(lines),
                    source_kind="deep_corpus_doc",
                    publication_state="loaded_to_dify",
                    review_state="review_required",
                    user_visible="no",
                    provenance_state="owner_curated",
                    notes="In Dify Deep_corpus; CORE_ROUTE_DEEP=off — not in user hot path",
                )
            )

    # Obsidian mirror (optional external path)
    if OBSIDIAN_RAG.is_dir():
        rows.append(
            ManifestRow(
                asset_id="OBS-RAG-ROOT",
                layer="operator",
                title="Obsidian WHIEDA RAG workspace",
                path=str(OBSIDIAN_RAG).replace("\\", "/"),
                format="folder",
                records_or_lines=str(sum(1 for _ in OBSIDIAN_RAG.rglob("*") if _.is_file())),
                source_kind="obsidian_vault",
                publication_state="local_only",
                review_state="review_required",
                provenance_state="owner_editor",
                user_visible="no",
                notes="Product cards RAW, medical package, chat-analysis, photo catalog",
            )
        )

    # Runtime SQL seed (local)
    seed = REPO_ROOT / "postgres" / "scripts" / "staging_seed_whieda_advisor_local_v1.sql"
    if seed.is_file():
        rows.append(
            ManifestRow(
                asset_id="SQL-SEED-LOCAL-CORE",
                layer="runtime_sql",
                title="Local Core advisor parity seed",
                path=rel_path(seed, REPO_ROOT),
                format="sql",
                records_or_lines=str(count_text_lines(seed)),
                source_kind="synthetic_fixture",
                publication_state="live_for_telegram_shadow_for_site",
                review_state="ok",
                provenance_state="synthetic_test",
                user_visible="local_core_only",
                notes="NOT production data; powers Docker parity lab",
            )
        )

    # QA corpora
    parity = QA_ROOT / "parity" / "core_local_parity_cases_v2.jsonl"
    if parity.is_file():
        info = read_jsonl_info(parity)
        rows.append(
            ManifestRow(
                asset_id="QA-PARITY-V2",
                layer="qa",
                title="Core local parity corpus v2",
                path=rel_path(parity, REPO_ROOT),
                format="jsonl",
                records_or_lines=str(info.valid_objects),
                source_kind="parity_cases",
                publication_state="staging",
                review_state="ok",
                provenance_state="engine_contract",
                user_visible="no",
                notes="85 HTTP cases P0+P1+P2; local Core only",
            )
        )

    acceptance = QA_ROOT / "cases" / "whieda_regression_cases_v1.jsonl"
    if acceptance.is_file():
        info = read_jsonl_info(acceptance)
        rows.append(
            ManifestRow(
                asset_id="QA-ACCEPTANCE-V1",
                layer="qa",
                title="WHIEDA regression acceptance corpus",
                path=rel_path(acceptance, REPO_ROOT),
                format="jsonl",
                records_or_lines=str(info.valid_objects),
                source_kind="acceptance_cases",
                publication_state="staging",
                review_state="ok",
                provenance_state="legacy_contract",
                user_visible="no",
                notes="265 cases; broader than parity v2",
            )
        )

    # Operator docs
    live_status = REPO_ROOT / "WHIEDA_LIVE_STATUS.md"
    if live_status.is_file():
        rows.append(
            ManifestRow(
                asset_id="DOC-LIVE-STATUS",
                layer="operator",
                title="Live production routing status",
                path=rel_path(live_status, REPO_ROOT),
                format="markdown",
                records_or_lines=str(count_text_lines(live_status)),
                source_kind="operator_doc",
                publication_state="unknown",
                review_state="ok",
                provenance_state="verified_readonly_2026-08-07",
                user_visible="no",
                notes="Source of truth for live vs shadow vs legacy routes",
            )
        )

    # Platform API advisor engine (code asset)
    engine = REPO_ROOT / "backend" / "platform-api" / "app" / "advisor" / "sql" / "engine.py"
    if engine.is_file():
        rows.append(
            ManifestRow(
                asset_id="CODE-ADVISOR-ENGINE",
                layer="runtime_sql",
                title="Structured SQL advisor engine",
                path=rel_path(engine, REPO_ROOT),
                format="python",
                records_or_lines=str(count_text_lines(engine)),
                source_kind="application_code",
                publication_state="staging",
                review_state="ok",
                provenance_state="implemented",
                user_visible="telegram_core_live; site_advisor_shadow",
                notes="Telegram Core cutover verified 2026-08-09; CORE_ROUTE_ADVISOR=shadow for site; local parity 85/85",
            )
        )

    return rows


def write_manifest_csv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
