"""Paths and taxonomy for WHIEDA read-only asset inventory."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAG_ROOT = REPO_ROOT / "RAG"
OBSIDIAN_RAG = Path(r"D:\Obsidian\WHIEDA\05_WHIEDA_RAG")
SQL_CORPUS = RAG_ROOT / "1 компиляция. диалоги с врачами" / "2_SQL_корпус_из_RAW"
DEEP_CORPUS = RAG_ROOT / "deep-corpus"
RAW_DIALOGUES = RAG_ROOT / "RAW диалоги с врачами"
RAW_TESTIMONIALS = RAW_DIALOGUES / "WHIEDA 🗣️ Отзывы.txt"

PLATFORM_API = REPO_ROOT / "backend" / "platform-api"
POSTGRES = REPO_ROOT / "postgres"
N8N_CURRENT = REPO_ROOT / "n8n" / "current"
QA_ROOT = REPO_ROOT / "qa"

MANIFEST_NAME = "WHIEDA_ASSET_MANIFEST_2026-08-09.csv"
FEATURE_MATRIX_NAME = "WHIEDA_FEATURE_STATUS_MATRIX_2026-08-09.md"
DATA_TO_FEATURE_NAME = "WHIEDA_DATA_TO_FEATURE_MAP_2026-08-09.md"

LAYERS = (
    "raw",
    "distillate",
    "structured_master",
    "runtime_sql",
    "deep_rag",
    "testimonials",
    "medical",
    "safety",
    "marketing",
    "bundles",
    "media",
    "qa",
    "workflow",
    "operator",
)

PUBLICATION_STATES = (
    "live",
    "staging",
    "loaded_to_dify",
    "local_only",
    "raw_only",
    "unknown",
)

FEATURE_STATUSES = (
    "LIVE_CONFIRMED",
    "IMPLEMENTED_NOT_LIVE_PROVEN",
    "DATA_READY_NOT_CONNECTED",
    "STAGING_OR_REVIEW",
    "MISSING",
    "BLOCKED_BY_OWNER_OR_DOCTOR",
)

# TSV layers that must not auto-publish (per distillate spec).
BLOCKED_DISTILLATE_LAYERS = frozenset(
    {
        "07_TESTIMONIALS.tsv",
        "09_BUNDLE_CANDIDATES.tsv",
        "10_MEDICAL_REVIEW_QUEUE.tsv",
        "15_SAFETY_SIGNALS.tsv",
        "16_COMMUNITY_BELIEFS.tsv",
        "17_CONTRADICTIONS.tsv",
        "18_DIALOGUE_FLOWS.tsv",
        "CORPUS_REPAIR_QUARANTINE.tsv",
    }
)

REVIEW_REQUIRED_LAYERS = frozenset(
    {
        "07_TESTIMONIALS.tsv",
        "06_USAGE_PATTERNS.tsv",
        "08_PROBLEM_SOLUTION_CANDIDATES.tsv",
        "09_BUNDLE_CANDIDATES.tsv",
        "10_MEDICAL_REVIEW_QUEUE.tsv",
        "11_MARKETING_LANGUAGE.tsv",
        "15_SAFETY_SIGNALS.tsv",
        "16_COMMUNITY_BELIEFS.tsv",
        "17_CONTRADICTIONS.tsv",
        "18_DIALOGUE_FLOWS.tsv",
    }
)

SKIP_PATH_PARTS = frozenset({"_backups", "passes", "reports", "raw_responses", "node_modules", ".git"})

MANIFEST_COLUMNS = (
    "asset_id",
    "layer",
    "title",
    "path",
    "format",
    "records_or_lines",
    "source_kind",
    "publication_state",
    "review_state",
    "provenance_state",
    "user_visible",
    "notes",
)
