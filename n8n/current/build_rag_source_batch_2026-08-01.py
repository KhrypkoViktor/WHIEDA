"""Build SQL quality source batch from local RAG corpus TSV exports."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAG = ROOT / "RAG" / "1 компиляция. диалоги с врачами" / "2_SQL_корпус_из_RAW"
OUT = Path(__file__).resolve().parent / "source_batches" / "rag_corpus_v1"


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def split_variants(value: str) -> list[str]:
    parts = []
    for chunk in value.replace('"', "").split(";"):
        line = chunk.strip()
        if line:
            parts.append(line)
    return parts


def write_lines(name: str, lines: list[str]) -> int:
    unique: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = " ".join(line.split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    target = OUT / name
    target.write_text("\n".join(unique) + "\n", encoding="utf-8")
    return len(unique)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    questions: list[str] = []
    for row in read_tsv(RAG / "01_QUESTIONS.tsv"):
        questions.append(row.get("нормализованный_вопрос") or row.get("исходный_вопрос") or "")

    faq: list[str] = []
    for row in read_tsv(RAG / "03_SQL_FAQ_CANDIDATES.tsv"):
        faq.append(row.get("канонический_вопрос") or "")
        faq.extend(split_variants(row.get("варианты_формулировки") or ""))

    objections: list[str] = []
    for row in read_tsv(RAG / "05_OBJECTIONS.tsv"):
        objections.append(row.get("возражение") or "")
        objections.extend(split_variants(row.get("варианты_формулировки") or ""))

    smoke_src = Path(__file__).resolve().parent / "source_batches" / "smoke_cases_sheet_v1" / "smoke_cases_input_text.txt"
    smoke_count = 0
    if smoke_src.exists():
        smoke_count = write_lines("smoke_cases_input_text.txt", smoke_src.read_text(encoding="utf-8").splitlines())

    counts = {
        "questions": write_lines("rag_questions.txt", questions),
        "faq": write_lines("rag_faq_questions.txt", faq),
        "objections": write_lines("rag_objections.txt", objections),
        "smoke_copied": smoke_count,
    }
    print(counts)


if __name__ == "__main__":
    main()
