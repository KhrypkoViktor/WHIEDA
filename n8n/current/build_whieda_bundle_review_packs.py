"""Build readable, non-publishing review packs for current WHIEDA bundle staging."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sql(query: str) -> list[dict]:
    fd, name = tempfile.mkstemp(prefix="whieda-review-packs-", suffix=".sql")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(query)
        result = subprocess.run(
            ["psql", "-X", "-Atq", "-v", "ON_ERROR_STOP=1", "-f", str(path)],
            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=90,
            env={**os.environ, "PGCLIENTENCODING": "UTF8"}, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        return json.loads(result.stdout or "[]")
    finally:
        path.unlink(missing_ok=True)


QUERY = r"""
WITH current_bundles AS (
  SELECT * FROM advisor_bundle_staging_records
  WHERE tenant_id='whieda' AND version_state='current'
), latest_calc AS (
  SELECT DISTINCT ON (bundle_staging_id) *
  FROM advisor_bundle_calculation_snapshots
  ORDER BY bundle_staging_id, calculated_at DESC
), items AS (
  SELECT bundle_staging_id, jsonb_agg(jsonb_build_object(
    'name', normalized_name, 'sku', sku, 'quantity', quantity,
    'status', item_status, 'stage', stage_text, 'dosage', dosage_text
  ) ORDER BY stage_text, normalized_name) AS rows
  FROM advisor_bundle_item_staging GROUP BY bundle_staging_id
), reviews AS (
  SELECT bundle_staging_id, jsonb_object_agg(review_type, queue_status) AS rows
  FROM advisor_bundle_review_queue GROUP BY bundle_staging_id
)
SELECT COALESCE(jsonb_agg(jsonb_build_object(
  'id', b.external_record_id, 'title', b.title, 'goal', b.goal_text,
  'logic', b.bundle_logic, 'primary', b.primary_product, 'additional', b.additional_products,
  'order', b.application_order, 'restrictions', b.restrictions_text,
  'expected', b.expected_result_text, 'raw_quote', b.raw_quote,
  'safety', b.safety_signals, 'block', b.block_reason,
  'medical_required', b.medical_review_required, 'business_required', b.business_review_required,
  'owner_approved', b.owner_approved, 'items', COALESCE(i.rows, '[]'::jsonb),
  'calc_state', c.calculation_state, 'retail_byn', c.retail_byn, 'retail_w', c.retail_w,
  'partner_byn', c.partner_byn, 'partner_w', c.partner_w, 'partner_pv', c.partner_pv,
  'missing_fields', c.details->'missing_fields', 'non_catalog', c.details->'non_catalog_statuses',
  'reviews', COALESCE(r.rows, '{}'::jsonb)
) ORDER BY b.external_record_id), '[]'::jsonb)
FROM current_bundles b
LEFT JOIN latest_calc c USING(bundle_staging_id)
LEFT JOIN items i USING(bundle_staging_id)
LEFT JOIN reviews r USING(bundle_staging_id);
"""


def clean(value: object) -> str:
    return str(value or "").strip() or "-"


def item_lines(rows: list[dict]) -> str:
    if not rows:
        return "- Состав ещё не разобран."
    result = []
    for item in rows:
        status = clean(item.get("status"))
        sku = f"; SKU {item['sku']}" if item.get("sku") else ""
        dosage = f"; {item['dosage']}" if item.get("dosage") else ""
        result.append(f"- {clean(item.get('name'))}: {item.get('quantity', 1)} шт.; {status}{sku}{dosage}")
    return "\n".join(result)


def heading(item: dict) -> str:
    return f"## {clean(item.get('id'))}. {clean(item.get('title'))}"


def main() -> None:
    rows = sql(QUERY)
    out = Path(__file__).resolve().parents[2] / "reviews" / "bundles"
    out.mkdir(parents=True, exist_ok=True)
    stamp = "2026-07-29"
    intro = (
        "# WHIEDA: бандлы на проверку\n\n"
        "Это staging-материалы. Ничего отсюда ещё не опубликовано в боте. "
        "Источник и исходная цитата сохранены для проверки.\n\n"
    )
    medical = [intro + "## Что проверить\nСостав, порядок, ограничения, опасные утверждения и исходную цитату.\n\n"]
    business = [intro + "## Что проверить\nНазвание, логику связки, кому предлагать и как описывать без медицинских обещаний.\n\n"]
    owner = [intro + "## Что проверить\nИтоговый список бандлов, стоимость, PV и текущие блокировки.\n\n"]
    for row in rows:
        base = heading(row) + "\n"
        safety = clean(row.get("safety"))
        medical.extend([
            base,
            f"**Состав**\n{item_lines(row.get('items') or [])}\n\n",
            f"**Порядок из источника**\n{clean(row.get('order'))}\n\n",
            f"**Ограничения из источника**\n{clean(row.get('restrictions'))}\n\n",
            f"**Safety-сигналы**\n{safety}\n\n",
            f"**Исходная цитата**\n> {clean(row.get('raw_quote'))}\n\n",
        ])
        business.extend([
            base,
            f"**Название и цель**\n{clean(row.get('title'))}: {clean(row.get('goal'))}\n\n",
            f"**Логика связки**\n{clean(row.get('logic'))}\n\n",
            f"**Состав**\n{item_lines(row.get('items') or [])}\n\n",
            f"**Какой ожидаемый результат заявлен в источнике**\n{clean(row.get('expected'))}\n\n",
            f"**Бизнес-блокировка**\n{clean(row.get('block'))}\n\n",
        ])
        prices = (
            f"Первичка: {clean(row.get('retail_w'))} W$ / {clean(row.get('retail_byn'))} BYN.  "
            f"Повторка: {clean(row.get('partner_w'))} W$ / {clean(row.get('partner_byn'))} BYN.  "
            f"PV: {clean(row.get('partner_pv'))}."
        )
        owner.extend([
            base,
            f"**Статус расчёта**: {clean(row.get('calc_state'))}\n\n",
            f"**Стоимость на текущем снимке каталога**\n{prices}\n\n",
            f"**Состав**\n{item_lines(row.get('items') or [])}\n\n",
            f"**Недостающие цены**: {clean(row.get('missing_fields'))}\n\n",
            f"**Не-каталожные элементы**: {clean(row.get('non_catalog'))}\n\n",
            f"**Очереди проверки**: {clean(row.get('reviews'))}\n\n",
            f"**Блокировка публикации**: {clean(row.get('block'))}\n\n",
        ])
    metadata = f"\n---\nСобрано: {datetime.now(timezone.utc).isoformat()} | Бандлов: {len(rows)} | Публикация: запрещена.\n"
    files = {
        out / f"WHIEDA_BUNDLES_MEDICAL_REVIEW_PACK_{stamp}.md": "".join(medical) + metadata,
        out / f"WHIEDA_BUNDLES_BUSINESS_REVIEW_PACK_{stamp}.md": "".join(business) + metadata,
        out / f"WHIEDA_BUNDLES_OWNER_REVIEW_PACK_{stamp}.md": "".join(owner) + metadata,
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    print(json.dumps({"bundles": len(rows), "files": [str(path) for path in files]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
