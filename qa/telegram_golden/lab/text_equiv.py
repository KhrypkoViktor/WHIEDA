"""Russian text equivalence helpers for Golden assertions."""

from __future__ import annotations


def normalize_ru(text: str) -> str:
    return str(text or "").casefold().replace("ё", "е")


def contains_normalized(haystack: str, needle: str) -> bool:
    return normalize_ru(needle) in normalize_ru(haystack)


def missing_needles(text: str, needles: list[str]) -> list[str]:
    return [needle for needle in needles or [] if not contains_normalized(text, str(needle))]


def forbidden_needles(text: str, needles: list[str]) -> list[str]:
    return [needle for needle in needles or [] if contains_normalized(text, str(needle))]
