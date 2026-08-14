"""Build discovery-map candidate rows from snapshot evidence."""

from __future__ import annotations

from dataclasses import dataclass

from constants import COLOR_ONLY, MANDATORY_GROUPS
from snapshot_loader import SnapshotBundle, aliases_for_phrase, normalize_phrase, product_name


@dataclass
class CandidateRow:
    phrase: str
    normalized_phrase: str
    discovery_group: str
    candidate_rank: int
    sku: str
    canonical_name: str
    confidence: str
    status: str
    evidence: str
    source_snapshot_id: str
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "phrase": self.phrase,
            "normalized_phrase": self.normalized_phrase,
            "discovery_group": self.discovery_group,
            "candidate_rank": str(self.candidate_rank),
            "sku": self.sku,
            "canonical_name": self.canonical_name,
            "confidence": self.confidence,
            "status": self.status,
            "evidence": self.evidence,
            "source_snapshot_id": self.source_snapshot_id,
            "notes": self.notes,
        }


def _ev(alias: str, sku: str, *, extra: str = "") -> str:
    bits = [f"aliases.tsv alias={alias!r} -> {sku}"]
    if extra:
        bits.append(extra)
    return "; ".join(bits)


def _rows_from_skus(
    *,
    phrase: str,
    group: str,
    snapshot_id: str,
    bundle: SnapshotBundle,
    skus: list[tuple[str, str, str, str, str]],
) -> list[CandidateRow]:
    rows: list[CandidateRow] = []
    for rank, (sku, confidence, status, evidence, notes) in enumerate(skus, start=1):
        if rank > 3:
            break
        rows.append(
            CandidateRow(
                phrase=phrase,
                normalized_phrase=normalize_phrase(phrase),
                discovery_group=group,
                candidate_rank=rank,
                sku=sku,
                canonical_name=product_name(bundle, sku) if sku else "",
                confidence=confidence,
                status=status,
                evidence=evidence,
                source_snapshot_id=snapshot_id,
                notes=notes,
            )
        )
    return rows


def _alias_backed(
    bundle: SnapshotBundle,
    phrase: str,
    group: str,
    *,
    default_status: str = "ready_for_core_review",
    max_candidates: int = 3,
) -> list[CandidateRow]:
    matches = aliases_for_phrase(bundle, phrase)
    if not matches:
        return []
    rows: list[CandidateRow] = []
    seen: set[str] = set()
    for row in matches:
        sku = str(row.get("canonical_sku") or "")
        if sku in seen:
            continue
        seen.add(sku)
        match_type = str(row.get("match_type") or "")
        priority = int(str(row.get("priority") or 0))
        answer_scope = str(row.get("answer_scope") or "")
        if match_type == "weak" or priority <= 15 or answer_scope == "clarify_only":
            status = "needs_owner_review" if sku else "do_not_resolve"
            confidence = "low"
        else:
            status = default_status
            confidence = "high" if priority >= 95 else "medium"
        rows.append(
            CandidateRow(
                phrase=phrase,
                normalized_phrase=normalize_phrase(phrase),
                discovery_group=group,
                candidate_rank=len(rows) + 1,
                sku=sku,
                canonical_name=product_name(bundle, sku),
                confidence=confidence,
                status=status,
                evidence=_ev(
                    str(row.get("alias") or phrase),
                    sku,
                    extra=f"priority={priority}; match_type={match_type}; answer_scope={answer_scope}",
                ),
                source_snapshot_id=bundle.snapshot_id,
                notes=str(row.get("notes") or "")[:180],
            )
        )
        if len(rows) >= max_candidates:
            break
    return rows


def build_rows(bundle: SnapshotBundle) -> list[CandidateRow]:
    sid = bundle.snapshot_id
    rows: list[CandidateRow] = []

    rows.extend(
        _rows_from_skus(
            phrase="эликсир",
            group="elixir",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("F001-02", "medium", "generic_category", "products.tsv Эликсир Фохоу", "3-color elixir family"),
                ("F003-02", "medium", "generic_category", "products.tsv Эликсир Саньцин", "3-color elixir family"),
                ("F002-02", "medium", "generic_category", "products.tsv Эликсир 3 Драгоценности", "3-color elixir family"),
            ],
        )
    )
    for phrase in ("красный эликсир", "зелёный эликсир", "синий эликсир"):
        backed = _alias_backed(bundle, phrase, "elixir")
        rows.extend(backed)

    rows.extend(
        _rows_from_skus(
            phrase="активатор",
            group="activator",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                (
                    "M015-00",
                    "high",
                    "generic_category",
                    _ev("активатор", "M015-00"),
                    "intentional ambiguity base+PRO; show_choices — never silent auto-open rank1",
                ),
                (
                    "EU-N000031-25",
                    "medium",
                    "generic_category",
                    _ev("активатор pro", "EU-N000031-25"),
                    "PRO variant in same choice menu",
                ),
            ],
        )
    )
    rows.extend(_alias_backed(bundle, "активатор про", "activator") or _rows_from_skus(
        phrase="активатор про",
        group="activator",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("EU-N000031-25", "high", "ready_for_core_review", _ev("активатор про", "EU-N000031-25"), "")],
    ))
    rows.extend(
        _rows_from_skus(
            phrase="pro",
            group="activator",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("EU-N000031-25", "medium", "needs_owner_review", _ev("активатор pro", "EU-N000031-25"), "short token; may need disambiguation"),
            ],
        )
    )

    for phrase in ("бэм", "magic", "массажер"):
        alias_rows = _alias_backed(bundle, phrase, "bem_magic")
        if alias_rows:
            rows.extend(alias_rows)
        elif phrase == "magic":
            rows.extend(
                _rows_from_skus(
                    phrase=phrase,
                    group="bem_magic",
                    snapshot_id=sid,
                    bundle=bundle,
                    skus=[
                        (
                            "EU-N000021-24",
                            "medium",
                            "needs_owner_review",
                            _ev("magic foherb", "EU-N000021-24"),
                            "bare token; show_choices per HLR — not direct-safe",
                        ),
                    ],
                )
            )
        elif phrase == "массажер":
            rows.extend(
                _rows_from_skus(
                    phrase=phrase,
                    group="bem_magic",
                    snapshot_id=sid,
                    bundle=bundle,
                    skus=[
                        (
                            "EU-N000021-24",
                            "medium",
                            "needs_owner_review",
                            _ev("массажer magic", "EU-N000021-24"),
                            "generic massager word",
                        ),
                    ],
                )
            )

    rows.extend(
        _rows_from_skus(
            phrase="паста",
            group="pasta",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("F071-00", "medium", "ready_for_core_review", "aliases.tsv паста/цинфэн family", "oral care paste vs tooth paste ambiguity"),
                ("EU-N000030-25", "medium", "ready_for_core_review", "aliases.tsv зубная паста", "tooth paste SKU in products.tsv"),
            ],
        )
    )
    for phrase in ("зубная паста", "паста с полынью"):
        rows.extend(_alias_backed(bundle, phrase, "pasta"))
    rows.extend(_alias_backed(bundle, "цинфэн", "pasta") or _rows_from_skus(
        phrase="цинфэн",
        group="pasta",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("F071-00", "high", "ready_for_core_review", _ev("цинфэн", "F071-00"), "")],
    ))

    rows.extend(_alias_backed(bundle, "стельки", "accessories") or _rows_from_skus(
        phrase="стельки",
        group="accessories",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("D013", "high", "ready_for_core_review", _ev("стельки", "D013"), "alias-backed; HLR still expects show_choices on nickname")],
    ))
    rows.extend(_alias_backed(bundle, "очки", "accessories") or _rows_from_skus(
        phrase="очки",
        group="accessories",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("D014", "high", "ready_for_core_review", _ev("очки", "D014"), "")],
    ))
    rows.extend(_alias_backed(bundle, "пояс", "accessories") or _rows_from_skus(
        phrase="пояс",
        group="accessories",
        snapshot_id=sid,
        bundle=bundle,
        skus=[
            ("T003", "medium", "needs_owner_review", _ev("пояс", "T003", extra="match_type=weak in aliases"), "weak alias; clarify size/intent"),
        ],
    ))
    rows.extend(
        _rows_from_skus(
            phrase="наколенники",
            group="accessories",
            snapshot_id=sid,
            bundle=bundle,
            skus=[("T001", "high", "ready_for_core_review", "products.tsv canonical_name Наколенники", "single SKU in snapshot")],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="шейная накладка",
            group="accessories",
            snapshot_id=sid,
            bundle=bundle,
            skus=[("T002-00", "high", "ready_for_core_review", "products.tsv canonical_name Шейная накладка", "single SKU in snapshot")],
        )
    )

    rows.extend(
        _rows_from_skus(
            phrase="косметика",
            group="cosmetics",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("C065-00", "medium", "generic_category", "products.tsv Fundesee gift/set cosmetics", "broad category"),
                ("C033-00", "medium", "generic_category", "products.tsv маска Fundesee", "broad category"),
                ("C002-00", "medium", "generic_category", "products.tsv увлажняющий гель", "broad category"),
            ],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="маска",
            group="cosmetics",
            snapshot_id=sid,
            bundle=bundle,
            skus=[("C033-00", "high", "ready_for_core_review", "products.tsv single маска SKU", "only one mask SKU in snapshot")],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="гель",
            group="cosmetics",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("D011-00", "high", "ready_for_core_review", _ev("очищающий гель foherb", "D011-00"), "Foherb hygiene gel"),
                ("C002-00", "medium", "ready_for_core_review", "products.tsv Увлажняющий гель", "Fundesee cosmetic gel"),
            ],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="шампунь",
            group="cosmetics",
            snapshot_id=sid,
            bundle=bundle,
            skus=[("EU-N000032-25", "high", "ready_for_core_review", "products.tsv красящий шампунь", "single shampoo SKU")],
        )
    )
    rows.append(
        CandidateRow(
            phrase="крем",
            normalized_phrase=normalize_phrase("крем"),
            discovery_group="cosmetics",
            candidate_rank=1,
            sku="",
            canonical_name="",
            confidence="low",
            status="needs_owner_review",
            evidence="products.tsv: no dedicated крем SKU in snapshot",
            source_snapshot_id=sid,
            notes="Category placeholder until owner maps a cream SKU",
        )
    )

    rows.extend(
        _rows_from_skus(
            phrase="капсулы",
            group="supplements",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("F028-00", "medium", "generic_category", "products.tsv capsule products", "multiple capsule SKUs"),
                ("F024-00", "medium", "generic_category", "products.tsv capsule products", "multiple capsule SKUs"),
                ("F007-00", "medium", "generic_category", "products.tsv capsule products", "multiple capsule SKUs"),
            ],
        )
    )
    rows.extend(_alias_backed(bundle, "чай", "supplements") or _rows_from_skus(
        phrase="чай",
        group="supplements",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("F031-00", "high", "ready_for_core_review", _ev("чай лювэй", "F031-00"), "")],
    ))
    rows.extend(_alias_backed(bundle, "кофе", "supplements") or _rows_from_skus(
        phrase="кофе",
        group="supplements",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("F034-00", "high", "ready_for_core_review", _ev("кофе с кордицепсом", "F034-00"), "")],
    ))
    rows.append(
        CandidateRow(
            phrase="бад",
            normalized_phrase=normalize_phrase("бад"),
            discovery_group="supplements",
            candidate_rank=1,
            sku="",
            canonical_name="",
            confidence="low",
            status="generic_category",
            evidence="products.tsv multiple supplement SKUs; no single БАД alias",
            source_snapshot_id=sid,
            notes="Open category — show capsule/supplement family, not one random SKU",
        )
    )

    rows.extend(_alias_backed(bundle, "сауна", "lifestyle") or _rows_from_skus(
        phrase="сауна",
        group="lifestyle",
        snapshot_id=sid,
        bundle=bundle,
        skus=[("M014-00", "high", "ready_for_core_review", _ev("сауна", "M014-00"), "")],
    ))
    rows.extend(
        _rows_from_skus(
            phrase="сон",
            group="lifestyle",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                (
                    "EU-N000014-24",
                    "high",
                    "ready_for_core_review",
                    "products.tsv Система для здорового сна 4 в 1",
                    "sleep kit",
                ),
            ],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="прибор для дома",
            group="lifestyle",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                ("M015-00", "medium", "generic_category", "products.tsv home devices", "device family"),
                ("M014-00", "medium", "generic_category", "products.tsv home devices", "device family"),
                ("EU-N000024-24", "medium", "generic_category", "products.tsv home devices", "device family"),
            ],
        )
    )
    rows.extend(
        _rows_from_skus(
            phrase="подарок",
            group="lifestyle",
            snapshot_id=sid,
            bundle=bundle,
            skus=[
                (
                    "C065-00",
                    "medium",
                    "generic_category",
                    "products.tsv подарочный набор Fundesee",
                    "task_selection on first turn; SKU buttons only after direction chosen",
                ),
                (
                    "EU-N000036-26",
                    "medium",
                    "generic_category",
                    "products.tsv подарочный набор x2",
                    "task_selection on first turn; never auto-open gift SKU",
                ),
            ],
        )
    )

    for color in COLOR_ONLY:
        rows.append(
            CandidateRow(
                phrase=color,
                normalized_phrase=normalize_phrase(color),
                discovery_group="color_guard",
                candidate_rank=1,
                sku="",
                canonical_name="",
                confidence="low",
                status="do_not_resolve",
                evidence=f"aliases.tsv weak/clarify_only for {color!r}; must not auto-open product",
                source_snapshot_id=sid,
                notes="Single colour token — require user clarification, not random product",
            )
        )

    return rows


def mandatory_phrase_coverage(rows: list[CandidateRow]) -> dict[str, list[str]]:
    covered: dict[str, set[str]] = {group: set() for group in MANDATORY_GROUPS}
    for row in rows:
        for group, phrases in MANDATORY_GROUPS.items():
            if row.phrase in phrases:
                covered[group].add(row.phrase)
    return {
        group: sorted(set(phrases) - covered[group])
        for group, phrases in MANDATORY_GROUPS.items()
        if set(phrases) - covered[group]
    }
