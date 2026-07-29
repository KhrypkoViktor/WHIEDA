-- Release 3.2: private normalized bundle calculations. Never consumed by runtime.
CREATE TABLE IF NOT EXISTS advisor_bundle_catalog_snapshots (
  snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  source_url text NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  source_hash text NOT NULL,
  UNIQUE(tenant_id, source_hash)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_catalog_snapshot_items (
  snapshot_id uuid NOT NULL REFERENCES advisor_bundle_catalog_snapshots(snapshot_id),
  sku text NOT NULL,
  canonical_name text NOT NULL,
  retail_price_rub numeric, retail_price_byn numeric, retail_w numeric,
  partner_price_rub numeric, partner_price_byn numeric, partner_w numeric, partner_pv numeric,
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY(snapshot_id,sku)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_item_staging (
  bundle_staging_id uuid NOT NULL REFERENCES advisor_bundle_staging_records(bundle_staging_id),
  source_name text NOT NULL,
  normalized_name text,
  sku text,
  quantity integer NOT NULL DEFAULT 1 CHECK(quantity > 0),
  item_status text NOT NULL DEFAULT 'unknown' CHECK(item_status IN ('catalog_product','generic','external','unknown')),
  stage_text text,
  dosage_text text,
  source_fragments jsonb NOT NULL DEFAULT '[]'::jsonb,
  match_state text NOT NULL DEFAULT 'unknown',
  match_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(bundle_staging_id,source_name)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_calculation_snapshots (
  calculation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bundle_staging_id uuid NOT NULL REFERENCES advisor_bundle_staging_records(bundle_staging_id),
  catalog_snapshot_id uuid NOT NULL REFERENCES advisor_bundle_catalog_snapshots(snapshot_id),
  calculation_state text NOT NULL CHECK(calculation_state IN ('fully_calculated','requires_binding','missing_price','awaiting_approval','partial_non_catalog')),
  retail_rub numeric, retail_byn numeric, retail_w numeric,
  partner_rub numeric, partner_byn numeric, partner_w numeric, partner_pv numeric,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(bundle_staging_id,catalog_snapshot_id)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_alias_dictionary (
  normalized_alias text PRIMARY KEY,
  canonical_sku text NOT NULL,
  canonical_name text NOT NULL,
  source_kind text NOT NULL CHECK (source_kind IN ('catalog','curated_bundle')),
  review_status text NOT NULL DEFAULT 'approved_internal' CHECK (review_status IN ('approved_internal','needs_review')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Release 3.2 migration for Release 3.1 databases.
ALTER TABLE advisor_bundle_item_staging
  ADD COLUMN IF NOT EXISTS normalized_name text,
  ADD COLUMN IF NOT EXISTS item_status text,
  ADD COLUMN IF NOT EXISTS stage_text text,
  ADD COLUMN IF NOT EXISTS dosage_text text,
  ADD COLUMN IF NOT EXISTS source_fragments jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE advisor_bundle_item_staging
  DROP CONSTRAINT IF EXISTS advisor_bundle_item_staging_match_state_check;
UPDATE advisor_bundle_item_staging
SET item_status = CASE match_state
  WHEN 'matched' THEN 'catalog_product'
  WHEN 'inactive' THEN 'unknown'
  WHEN 'ambiguous' THEN 'unknown'
  WHEN 'unresolved' THEN 'unknown'
  ELSE COALESCE(item_status, 'unknown')
END,
match_state = CASE match_state
  WHEN 'matched' THEN 'catalog_product'
  WHEN 'inactive' THEN 'unknown'
  WHEN 'ambiguous' THEN 'unknown'
  WHEN 'unresolved' THEN 'unknown'
  ELSE match_state
END;
ALTER TABLE advisor_bundle_item_staging
  ADD CONSTRAINT advisor_bundle_item_staging_match_state_check
  CHECK(match_state IN ('catalog_product','generic','external','unknown'));
ALTER TABLE advisor_bundle_item_staging
  ALTER COLUMN item_status SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_bundle_item_normalized
  ON advisor_bundle_item_staging(bundle_staging_id, normalized_name)
  WHERE normalized_name IS NOT NULL;

ALTER TABLE advisor_bundle_calculation_snapshots
  DROP CONSTRAINT IF EXISTS advisor_bundle_calculation_snapshots_calculation_state_check;
ALTER TABLE advisor_bundle_calculation_snapshots
  ADD CONSTRAINT advisor_bundle_calculation_snapshots_calculation_state_check
  CHECK(calculation_state IN ('fully_calculated','requires_binding','missing_price','awaiting_approval','partial_non_catalog'));
