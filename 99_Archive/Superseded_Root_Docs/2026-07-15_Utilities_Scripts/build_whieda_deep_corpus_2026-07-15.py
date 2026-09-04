"""Create Dify-ready, source-labelled deep documents without shortening originals."""

from pathlib import Path


RAW_ROOT = Path(r"D:\Obsidian\WHIEDA\05_WHIEDA_RAG")
OUT_ROOT = Path(r"D:\Projects\WHIEDA\RAG\deep-corpus")

SOURCES = [
    ("SRC-ACT-01", "Активатор клеток 1 часть.md", "Активатор клеток", "manufacturer_claim", "needs_medical_review"),
    ("SRC-ACT-02", "Активатор клеток 2 часть.md", "Активатор клеток", "manufacturer_claim", "needs_medical_review"),
    ("SRC-WEN-01", "Вентун 1 часть.md", "Вэнтун", "manufacturer_claim", "needs_medical_review"),
    ("SRC-WEN-02", "Вентун 2 часть.md", "Вэнтун", "manufacturer_claim", "needs_medical_review"),
    ("SRC-BAG-01", "Ба-гуа_часть 1.md", "Ба-Гуа", "manufacturer_claim", "needs_medical_review"),
    ("SRC-BAG-02", "Ба-гуа_часть 2.md", "Ба-Гуа", "manufacturer_claim", "needs_medical_review"),
    ("SRC-BEM-01", "Биоэнергомассажер (БЭМ) Magic FoHerb 3.0_1 часть.md", "Magic FoHerb 3.0", "manufacturer_claim", "needs_medical_review"),
    ("SRC-BEM-02", "Биоэнергомассажер (БЭМ) Magic FoHerb 3.0_2 часть.md", "Magic FoHerb 3.0", "manufacturer_claim", "needs_medical_review"),
    ("SRC-SAFETY-01", "Единые показания - противопоказания.md", "Все товары", "safety_reference", "needs_medical_review"),
    ("SRC-VOICE-01", "Правила речи.md", "Voice", "owner_guidance", "needs_business_review"),
    ("SRC-BUSINESS-01", "Пакет 10. Маркетинг-план и стратегия лидерской ветки..md", "Маркетинг-план", "company_training", "needs_business_review"),
]


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    generated = []
    for source_id, filename, product, evidence, review in SOURCES:
        original = RAW_ROOT / filename
        body = original.read_text(encoding="utf-8")
        target = OUT_ROOT / f"{source_id}__{filename}"
        header = (
            "---\n"
            f"source_id: {source_id}\n"
            f"product_or_topic: {product}\n"
            f"evidence_status: {evidence}\n"
            f"review_status: {review}\n"
            "publication_scope: internal_rag_only\n"
            f"original_path: {original}\n"
            "rule: Original content below is complete. Do not turn experience, training or claims into medical proof.\n"
            "---\n\n"
        )
        target.write_text(header + body, encoding="utf-8")
        generated.append(target.name)
    manifest = OUT_ROOT / "README.md"
    manifest.write_text(
        "# WHIEDA deep corpus\n\n"
        "Файлы содержат полные оригинальные тексты с source metadata. "
        "Загружать в Dify как внутреннюю библиотеку; public-ответы допускаются только после соответствующего review_status.\n\n"
        + "\n".join(f"- `{name}`" for name in generated)
        + "\n",
        encoding="utf-8",
    )
    print(f"generated={len(generated)} out={OUT_ROOT}")


if __name__ == "__main__":
    main()
