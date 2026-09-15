"""Rule-based wording audit for product cards."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from parser import CARD_SECTIONS, POLICY_FIELDS, WORDING_FIELDS, CardRecord, parse_field_provenance

OPENING_FORMULAE = (
    "это ваш",
    "это ваш ",
    "это ваш личный",
    "это ваш незаметный",
    "это гигиенический продукт",
    "это ваш мягкий",
)

VAGUE_PHRASES = (
    "работа с зоной дискомфорта",
    "работа с зонами дискомфорта",
    "локальная работа",
    "мягкая поддержка",
    "мягко поработал",
    "мягкая локальная",
    "нейропластич",
    "универсальный домашний спасатель",
    "не волшебство",
    "очень практичная вещь",
)

NEURO_MARKERS = (
    "нейропластич",
    "нейромедиатор",
    "нейро ",
    "brain hack",
)

MAX_FIELD_CHARS = 900
MAX_PARAGRAPH_SENTENCES = 6


@dataclass
class Finding:
    finding_id: str
    severity: str
    sku: str
    source_layer: str
    section: str
    matched_span: str
    rule_id: str
    reason: str
    label: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "sku": self.sku,
            "source_layer": self.source_layer,
            "section": self.section,
            "matched_span": self.matched_span,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "label": self.label,
        }


def _norm(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    return re.sub(r"\s+", " ", lowered)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _bullets(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"[;•\n]+", str(text or "")) if b.strip()]


def _next_id(counter: dict[str, int], prefix: str) -> str:
    counter[prefix] = counter.get(prefix, 0) + 1
    return f"{prefix}-{counter[prefix]:03d}"


def audit_card(card: CardRecord, *, id_counter: dict[str, int]) -> list[Finding]:
    findings: list[Finding] = []
    row = card.row
    title = str(row.get("canonical_name") or row.get("short_name") or "")

    for field in WORDING_FIELDS:
        text = str(row.get(field) or "").strip()
        if not text and field in {
            "what_it_is",
            "who_asks_about_it",
            "common_use_cases",
            "contraindications_short",
        }:
            findings.append(
                Finding(
                    finding_id=_next_id(id_counter, "PCW"),
                    severity="attention",
                    sku=card.sku,
                    source_layer=card.source_layer,
                    section=field,
                    matched_span="",
                    rule_id="SHAPE-EMPTY",
                    reason="Important section is empty.",
                )
            )
            continue
        if not text:
            continue

        norm = _norm(text)
        for phrase in VAGUE_PHRASES:
            if phrase in norm:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=phrase,
                        rule_id="VAGUE-PHRASE",
                        reason="Flagged vague or cliché phrase from writing guidelines.",
                        label="needs human rewrite",
                    )
                )

        for marker in NEURO_MARKERS:
            if marker in norm:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=marker,
                        rule_id="NEURO-TEXT",
                        reason="Potentially neuro-marketing wording; owner review required.",
                        label="needs human rewrite",
                    )
                )

        if field == "what_it_is":
            for opening in OPENING_FORMULAE:
                if norm.startswith(opening):
                    findings.append(
                        Finding(
                            finding_id=_next_id(id_counter, "PCW"),
                            severity="review",
                            sku=card.sku,
                            source_layer=card.source_layer,
                            section=field,
                            matched_span=opening,
                            rule_id="OPENING-FORMULA",
                            reason="Repeated opening formula across cards.",
                        )
                    )
                    break

        if title and _norm(title) in norm and field != "what_it_is":
            hits = len(re.findall(re.escape(_norm(title)), norm))
            if hits >= 2:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=title,
                        rule_id="TITLE-REPEAT",
                        reason="Product title repeated inside body text.",
                    )
                )

        if len(text) > MAX_FIELD_CHARS:
            findings.append(
                Finding(
                    finding_id=_next_id(id_counter, "PCW"),
                    severity="review",
                    sku=card.sku,
                    source_layer=card.source_layer,
                    section=field,
                    matched_span=text[:80] + "…",
                    rule_id="SHAPE-LONG",
                    reason=f"Section exceeds {MAX_FIELD_CHARS} characters.",
                )
            )

        if field in {"what_it_is", "who_asks_about_it", "how_to_use_short", "what_to_expect_soft"}:
            if len(_sentences(text)) >= MAX_PARAGRAPH_SENTENCES and ";" not in text:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=text[:80] + "…",
                        rule_id="SHAPE-WALL",
                        reason="One long unbroken paragraph; consider splitting for Telegram scan.",
                    )
                )

        if field == "common_use_cases":
            bullets = _bullets(text)
            dupes = [b for b, c in Counter(_norm(x) for x in bullets).items() if c > 1 and b]
            if dupes:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=dupes[0],
                        rule_id="REPEAT-BULLET",
                        reason="Duplicated bullet inside common_use_cases.",
                    )
                )
            if ";;" in text or text.count(";") >= 8:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=";",
                        rule_id="SHAPE-BULLET",
                        reason="Malformed or overloaded bullet separators.",
                    )
                )

        if field in POLICY_FIELDS and any(
            token in norm for token in ("уточнить", "осторожно", "по необходимости", "может")
        ):
            if len(text) < 40:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=text[:80],
                        rule_id="RESTRICT-VAGUE",
                        reason="Restriction-like field with vague wording; human review recommended.",
                    )
                )

        sentences = [_norm(s) for s in _sentences(text)]
        for sent, count in Counter(sentences).items():
            if count > 1 and len(sent) > 24:
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="review",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=sent[:80],
                        rule_id="REPEAT-SENTENCE",
                        reason="Repeated sentence inside the same card field.",
                    )
                )
                break

    if card.source_layer == "candidate":
        if not str(row.get("source_refs") or "").strip():
            findings.append(
                Finding(
                    finding_id=_next_id(id_counter, "PCW"),
                    severity="attention",
                    sku=card.sku,
                    source_layer=card.source_layer,
                    section="source_refs",
                    matched_span="",
                    rule_id="PROV-MISSING-REF",
                    reason="Candidate missing source_refs.",
                )
            )
        if not str(row.get("provenance_status") or "").strip():
            findings.append(
                Finding(
                    finding_id=_next_id(id_counter, "PCW"),
                    severity="attention",
                    sku=card.sku,
                    source_layer=card.source_layer,
                    section="provenance_status",
                    matched_span="",
                    rule_id="PROV-MISSING-STATUS",
                    reason="Candidate missing provenance_status.",
                )
            )
        provenance = parse_field_provenance(row)
        for field in CARD_SECTIONS:
            status = provenance.get(field, "")
            if status.startswith("needs_owner_review"):
                findings.append(
                    Finding(
                        finding_id=_next_id(id_counter, "PCW"),
                        severity="attention",
                        sku=card.sku,
                        source_layer=card.source_layer,
                        section=field,
                        matched_span=status,
                        rule_id="PROV-NEEDS-REVIEW",
                        reason="Field provenance marked needs_owner_review.",
                    )
                )

    return findings


def audit_cross_card(cards: list[CardRecord], *, id_counter: dict[str, int]) -> list[Finding]:
    findings: list[Finding] = []
    sentence_map: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        for field in WORDING_FIELDS:
            text = str(card.row.get(field) or "")
            for sent in _sentences(text):
                norm = _norm(sent)
                if len(norm) >= 48:
                    sentence_map[norm].append(f"{card.sku}:{field}")

    for sent, locations in sentence_map.items():
        skus = {loc.split(":", 1)[0] for loc in locations}
        if len(skus) >= 2:
            findings.append(
                Finding(
                    finding_id=_next_id(id_counter, "PCW"),
                    severity="review",
                    sku=sorted(skus)[0],
                    source_layer="cross_card",
                    section=locations[0].split(":", 1)[1],
                    matched_span=sent[:120],
                    rule_id="REPEAT-CROSS",
                    reason=f"Same sentence appears across cards: {', '.join(sorted(skus)[:4])}.",
                )
            )
    return findings


def run_audit(cards: list[CardRecord]) -> list[Finding]:
    id_counter: dict[str, int] = {}
    findings: list[Finding] = []
    for card in cards:
        findings.extend(audit_card(card, id_counter=id_counter))
    findings.extend(audit_cross_card(cards, id_counter=id_counter))
    return findings
