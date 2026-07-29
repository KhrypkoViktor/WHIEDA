-- Release 3.1: private calculation staging. Never consumed by runtime.
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
  source_name text NOT NULL, sku text, quantity integer NOT NULL DEFAULT 1 CHECK(quantity > 0),
  match_state text NOT NULL CHECK(match_state IN ('matched','ambiguous','unresolved','inactive')),
  match_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(bundle_staging_id,source_name)
);
CREATE TABLE IF NOT EXISTS advisor_bundle_calculation_snapshots (
  calculation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bundle_staging_id uuid NOT NULL REFERENCES advisor_bundle_staging_records(bundle_staging_id),
  catalog_snapshot_id uuid NOT NULL REFERENCES advisor_bundle_catalog_snapshots(snapshot_id),
  calculation_state text NOT NULL CHECK(calculation_state IN ('fully_calculated','requires_binding','missing_price','awaiting_approval')),
  retail_rub numeric, retail_byn numeric, retail_w numeric,
  partner_rub numeric, partner_byn numeric, partner_w numeric, partner_pv numeric,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(bundle_staging_id,catalog_snapshot_id)
);
