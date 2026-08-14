"""Product discovery map constants."""

from __future__ import annotations

TSV_COLUMNS = (
    "phrase",
    "normalized_phrase",
    "discovery_group",
    "candidate_rank",
    "sku",
    "canonical_name",
    "confidence",
    "status",
    "evidence",
    "source_snapshot_id",
    "notes",
)

STATUSES = frozenset(
    {"ready_for_core_review", "needs_owner_review", "generic_category", "do_not_resolve"}
)
CONFIDENCE = frozenset({"high", "medium", "low"})

MANDATORY_GROUPS: dict[str, list[str]] = {
    "elixir": ["эликсир", "красный эликсир", "зелёный эликсир", "синий эликсир"],
    "activator": ["активатор", "активатор про", "pro"],
    "bem_magic": ["бэм", "magic", "массажер"],
    "pasta": ["паста", "зубная паста", "цинфэн", "паста с полынью"],
    "accessories": ["стельки", "очки", "пояс", "наколенники", "шейная накладка"],
    "cosmetics": ["косметика", "маска", "гель", "шампунь", "крем"],
    "supplements": ["капсулы", "чай", "кофе", "бад"],
    "lifestyle": ["сауна", "сон", "прибор для дома", "подарок"],
}

COLOR_ONLY = ("красный", "зелёный", "синий")
