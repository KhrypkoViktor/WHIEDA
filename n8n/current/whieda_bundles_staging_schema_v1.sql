-- Release 3 Bundles: internal staging and review only. No runtime publication.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS advisor_bundle_import_runs (
  import_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  run_state text NOT NULL DEFAULT 'running' CHECK (run_state IN ('running','completed','failed','rolled_back')),
  source_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
  error_text text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS advisor_bundle_staging_records (
  bundle_staging_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  external_record_id text NOT NULL,
  content_hash text NOT NULL,
  source_id text NOT NULL,
  source_locator text,
  raw_quote text,
  title text NOT NULL,
  goal_text text,
  primary_product text,
  additional_products text,
  bundle_logic text,
  application_order text,
  restrictions_text text,
  expected_result_text text,
  safety_signals jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_status text,
  publication_status text NOT NULL DEFAULT 'blocked_raw',
  medical_review_required boolean NOT NULL DEFAULT false,
  business_review_required boolean NOT NULL DEFAULT false,
  owner_approved boolean NOT NULL DEFAULT false,
  block_reason text,
  version_state text NOT NULL DEFAULT 'current' CHECK (version_state IN ('current','superseded')),
  superseded_by uuid,
  first_seen_run_id uuid NOT NULL REFERENCES advisor_bundle_import_runs(import_run_id),
  last_seen_run_id uuid NOT NULL REFERENCES advisor_bundle_import_runs(import_run_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id, external_record_id, content_hash)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_run_records (
  import_run_id uuid NOT NULL REFERENCES advisor_bundle_import_runs(import_run_id),
  bundle_staging_id uuid NOT NULL REFERENCES advisor_bundle_staging_records(bundle_staging_id),
  action text NOT NULL CHECK (action IN ('inserted','reused','new_version','rejected')),
  PRIMARY KEY(import_run_id, bundle_staging_id)
);

CREATE TABLE IF NOT EXISTS advisor_bundle_review_queue (
  review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bundle_staging_id uuid NOT NULL REFERENCES advisor_bundle_staging_records(bundle_staging_id),
  review_type text NOT NULL CHECK (review_type IN ('medical','business','owner','provenance','safety')),
  queue_status text NOT NULL DEFAULT 'pending',
  last_evaluated_run_id uuid NOT NULL REFERENCES advisor_bundle_import_runs(import_run_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(bundle_staging_id, review_type)
);

CREATE INDEX IF NOT EXISTS idx_bundle_staging_current ON advisor_bundle_staging_records(tenant_id, version_state, publication_status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bundle_one_current_version
  ON advisor_bundle_staging_records(tenant_id, external_record_id)
  WHERE version_state = 'current';

-- Release 3.2 migration for databases created before safety signals existed.
ALTER TABLE advisor_bundle_staging_records
  ADD COLUMN IF NOT EXISTS safety_signals jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS advisor_bundle_alias_dictionary (
  normalized_alias text PRIMARY KEY,
  canonical_sku text NOT NULL,
  canonical_name text NOT NULL,
  source_kind text NOT NULL CHECK (source_kind IN ('catalog','curated_bundle')),
  review_status text NOT NULL DEFAULT 'approved_internal' CHECK (review_status IN ('approved_internal','needs_review')),
  updated_at timestamptz NOT NULL DEFAULT now()
);
