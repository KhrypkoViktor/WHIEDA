"""Product alias resolution — ported scoring from legacy structured lookup."""

from __future__ import annotations

from typing import Any

from app.advisor.sql.text import has_pro_marker, normalize_text, product_query_text

# Common user typos observed in live chats (not always present as DB aliases).
QUERY_TYPO_MAP = {
    "фузялина": "спирулина",
    "сперулина": "спирулина",
    "спирулинаа": "спирулина",
    "активаторр": "активатор клеток",
    "ативатор": "активатор клеток",
    "ленжи": "линчжи",
    "леньчжи": "линчжи",
    "линьчжи": "линчжи",
    "вентум": "вэнтун",
    "веетуна": "вэнтун",
    "винтун": "вэнтун",
    "вэнтум": "вэнтун",
    "wentun": "вэнтун",
    "ба гоа": "ба-гуа",
    "бо гуа": "ба-гуа",
    "бэмчик": "бэм",
    "вэм": "бэм",
    "санцин": "саньцин",
    "санцын": "саньцин",
    "сяньцинь": "саньцин",
    "фоху": "фохоу",
    "фохуа": "фохоу",
    "фухоу": "фохоу",
    "полонтино": "палантин",
    "полын": "паста с экстрактом полыни",
    "полыни": "паста с экстрактом полыни",
    "кампьютерные очки": "очки для компьютера",
}


def normalize_compact(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def levenshtein_distance(left: str, right: str, max_distance: int = 2) -> int:
    a = str(left or "")
    b = str(right or "")
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    prev_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        row_min = i
        for j, char_b in enumerate(b, start=1):
            cost = 0 if char_a == char_b else 1
            value = min(current[j - 1] + 1, prev_row[j] + 1, prev_row[j - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return max_distance + 1
        prev_row = current
    return prev_row[-1]


def fuzzy_single_word_match(text: str, alias: str) -> bool:
    phrase = normalize_text(alias)
    if not phrase or " " in phrase or len(phrase) < 6:
        return False
    tokens = [token for token in normalize_text(text).split() if token]
    return any(
        len(token) >= 5 and levenshtein_distance(token, phrase, 1) <= 1 for token in tokens
    )


def _product_name_match_score(canonical_name: str, normalized_text: str) -> tuple[bool, int]:
    alias = normalize_text(canonical_name)
    if not alias:
        return False, 0
    if alias in normalized_text:
        return True, 1000 + len(alias)
    stop_words = {"набор", "комплект", "косметики", "продукции", "whieda"}
    tokens = [token for token in alias.split() if len(token) > 1 and token not in stop_words]
    if not tokens:
        return False, 0
    matched = [token for token in tokens if token in normalized_text]
    has_specific = any(token.isascii() or any(ch.isdigit() for ch in token) or len(token) >= 5 for token in matched)
    ratio = len(matched) / len(tokens)
    matched_ok = has_specific and len(matched) >= 2 and ratio >= 0.5
    return matched_ok, len(matched) * 100 + int(ratio * 10)


def score_alias_candidate(row: dict[str, Any], question: str) -> int | None:
    normalized = normalize_text(question)
    compact = normalize_compact(question)
    alias = normalize_text(str(row.get("alias") or ""))
    if not alias:
        return None
    alias_compact = normalize_compact(alias)
    match_type = normalize_text(str(row.get("match_type") or "alias"))
    exact = normalized == alias or compact == alias_compact
    partial = alias in normalized or alias_compact in compact
    fuzzy = fuzzy_single_word_match(normalized, alias)
    if match_type == "weak" and not exact:
        return None
    if not (exact or partial or fuzzy):
        return None
    score = int(row.get("priority") or 0)
    if exact:
        score += 10000 + len(alias)
    elif partial:
        score += 2000 + len(alias)
    else:
        score += 500 + len(alias)
    return score


def pick_best_product(
    question: str,
    alias_rows: list[dict[str, Any]],
    *,
    allow_pro: bool | None = None,
) -> dict[str, Any] | None:
    if allow_pro is None:
        allow_pro = has_pro_marker(question)

    scored: list[tuple[int, dict[str, Any]]] = []
    normalized = normalize_text(question)
    seen_skus: set[str] = set()

    for row in alias_rows:
        score = score_alias_candidate(row, question)
        if score is None:
            continue
        blob = f"{row.get('alias', '')} {row.get('canonical_name', '')}"
        if not allow_pro and has_pro_marker(blob):
            continue
        sku = str(row.get("sku") or "")
        if sku in seen_skus:
            continue
        seen_skus.add(sku)
        scored.append((score, row))

    for row in alias_rows:
        name = str(row.get("canonical_name") or "")
        matched, score = _product_name_match_score(name, normalized)
        if not matched:
            continue
        if not allow_pro and has_pro_marker(name):
            continue
        sku = str(row.get("sku") or "")
        if sku in seen_skus:
            continue
        seen_skus.add(sku)
        scored.append((score, row))

    if not scored:
        return None
    if allow_pro:
        pro_scored = [
            item
            for item in scored
            if has_pro_marker(str(item[1].get("canonical_name") or ""))
            or has_pro_marker(str(item[1].get("alias") or ""))
        ]
        if pro_scored:
            scored = pro_scored
    scored.sort(key=lambda item: (item[0], len(str(item[1].get("alias") or ""))), reverse=True)
    return scored[0][1]


async def resolve_product(
    conn,
    tenant_id: str,
    question: str,
    sku: str | None,
    slug: str | None,
    *,
    repo,
) -> dict[str, Any] | None:
    if sku:
        row = await repo.resolve_product_by_sku(conn, tenant_id, sku)
        if row:
            return row

    lookup = product_query_text(question)
    if not lookup:
        return None

    if has_pro_marker(question) and "активатор" in normalize_text(question):
        pro_row = await repo.resolve_activator_pro_product(conn, tenant_id)
        if pro_row:
            return pro_row

    if has_pro_marker(question):
        pro_candidates = await repo.fetch_alias_candidates(conn, tenant_id, "активатор клеток pro")
        pro_best = pick_best_product("активатор клеток pro", pro_candidates, allow_pro=True)
        if pro_best and has_pro_marker(str(pro_best.get("canonical_name") or "")):
            return pro_best

    normalized = normalize_text(lookup)
    typo_target = QUERY_TYPO_MAP.get(normalized)
    if typo_target:
        normalized = typo_target
        lookup = typo_target
    row = await repo.resolve_product_by_exact_alias(conn, tenant_id, normalized)
    if row:
        if has_pro_marker(question):
            if has_pro_marker(str(row.get("canonical_name") or "")):
                return row
        elif not has_pro_marker(str(row.get("canonical_name") or "")):
            return row

    candidates = await repo.fetch_alias_candidates(conn, tenant_id, lookup)
    best = pick_best_product(lookup, candidates, allow_pro=has_pro_marker(question))
    if best:
        return best

    if slug:
        return await repo.resolve_product_by_slug(conn, tenant_id, slug)
    return None
