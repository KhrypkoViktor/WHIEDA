"""Build a reviewable WHIEDA media catalogue from the existing RAG link register."""

import csv
import hashlib
import re
from pathlib import Path


RAW_ROOT = Path(r"D:\Obsidian\WHIEDA\05_WHIEDA_RAG")
SOURCE_PATH = RAW_ROOT / "Ссылки.md"
OUTPUT_PATH = Path(r"D:\Projects\WHIEDA\RAG\WHIEDA_RAG_MEDIA_CATALOG_V1_2026-07-15.csv")
SOURCE_CATALOG_PATH = Path(r"D:\Projects\WHIEDA\RAG\WHIEDA_RAG_SOURCE_CATALOG_V1_2026-07-15.csv")


def clean(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"[*_`]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def classify_depth(blob: str) -> str:
    return "deep" if re.search(r"фундаментальн|клиническ|мастер[ -]?класс|профессиональн|газнели|час", blob, re.I) else "surface"


def classify_risk(blob: str) -> str:
    return "medical_review_required" if re.search(
        r"онколог|диабет|тромб|варикоз|лечение|воспалени|аутоиммун|гинеколог|микрофлор|давлен|детокс|токсин|диагност",
        blob,
        re.I,
    ) else "business_or_product_review"


def main() -> None:
    text = SOURCE_PATH.read_text(encoding="utf-8")
    rows = []
    seen = set()
    for line in text.splitlines():
        urls = re.findall(r"https?://[^\s|)]+", line)
        if not urls or not line.lstrip().startswith("|"):
            continue
        cells = [clean(cell) for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() in {"продукт", "тема", ":---"}:
            continue
        product = cells[0]
        description = cells[1]
        title = cells[2] if len(cells) > 2 else ""
        for url in urls:
            key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            if key in seen:
                continue
            seen.add(key)
            blob = " ".join((product, description, title))
            rows.append({
                "source_id": f"VID-{key}",
                "product_or_topic": product,
                "title": title,
                "url": url.rstrip(".,"),
                "description": description,
                "depth": classify_depth(blob),
                "source_class": "company_training_or_presentation",
                "evidence_status": "manufacturer_claim",
                "review_status": "needs_medical_review" if classify_risk(blob) == "medical_review_required" else "needs_business_review",
                "publication_scope": "internal_rag_only",
                "risk_flag": classify_risk(blob),
                "original_source": str(SOURCE_PATH),
            })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    source_rows = []
    for source in sorted(RAW_ROOT.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in {".md", ".txt", ".csv"}:
            continue
        relative = source.relative_to(RAW_ROOT).as_posix()
        name = source.name.lower()
        blob = relative.lower()
        if "raw экспорты" in blob:
            ingestion = "full_archive_only"
            review_status = "needs_cluster_review"
        elif "противопоказ" in blob or "ограничен" in blob:
            ingestion = "safety_priority"
            review_status = "needs_medical_review"
        elif any(marker in blob for marker in ("активатор", "вентун", "ба-гуа", "бэм", "пакет 1", "пакет 2", "пакет 4", "пакет 8")):
            ingestion = "rag_candidate_full_text"
            review_status = "needs_medical_review"
        elif any(marker in blob for marker in ("маркетинг", "правила речи", "content-", "content_")):
            ingestion = "rag_candidate_full_text"
            review_status = "needs_business_review"
        else:
            ingestion = "rag_candidate_full_text"
            review_status = "needs_review"
        digest = hashlib.sha1(source.read_bytes()).hexdigest()[:12]
        source_rows.append({
            "source_id": f"DOC-{digest}",
            "relative_path": relative,
            "bytes": source.stat().st_size,
            "ingestion_mode": ingestion,
            "review_status": review_status,
            "original_source": str(source),
        })
    with SOURCE_CATALOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]) if source_rows else [])
        writer.writeheader()
        writer.writerows(source_rows)
    print(f"media_rows={len(rows)} source_rows={len(source_rows)} output={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
