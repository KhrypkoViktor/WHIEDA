-- WHIEDA distillate staging. This schema is provenance-only and is never
-- queried by the advisor runtime workflow.

CREATE TABLE IF NOT EXISTS advisor_distillate_import_runs (
  import_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  source_dir text NOT NULL,
  source_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
  validation_status text NOT NULL,
  staged_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  report jsonb NOT NULL DEFAULT '{}'::jsonb,
  rollback_at timestamptz,
  rollback_reason text
);

CREATE TABLE IF NOT EXISTS advisor_distillate_records (
  staging_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_run_id uuid NOT NULL REFERENCES advisor_distillate_import_runs(import_run_id),
  tenant_id text NOT NULL,
  source_table text NOT NULL,
  external_record_id text NOT NULL,
  source_id text NOT NULL,
  source_locator text,
  raw_quote text,
  source_type text,
  authority_level text,
  extraction_pass text,
  source_hash text NOT NULL,
  content_hash text NOT NULL,
  payload jsonb NOT NULL,
  publication_status text NOT NULL DEFAULT 'blocked_raw',
  medical_review_state text NOT NULL DEFAULT 'not_required',
  business_review_state text NOT NULL DEFAULT 'not_required',
  owner_state text NOT NULL DEFAULT 'not_approved',
  supersedes_staging_id uuid REFERENCES advisor_distillate_records(staging_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, source_table, external_record_id, content_hash)
);

CREATE TABLE IF NOT EXISTS advisor_distillate_quarantine (
  quarantine_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_run_id uuid REFERENCES advisor_distillate_import_runs(import_run_id),
  tenant_id text NOT NULL,
  source_file text NOT NULL,
  source_row_number integer,
  source_id text,
  raw_row jsonb,
  reason text NOT NULL,
  resolution_status text NOT NULL DEFAULT 'unresolved',
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS advisor_distillate_links (
  link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_run_id uuid NOT NULL REFERENCES advisor_distillate_import_runs(import_run_id),
  from_staging_id uuid NOT NULL REFERENCES advisor_distillate_records(staging_id),
  to_source_table text,
  to_external_record_id text,
  relation_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS advisor_distillate_review_queue (
  review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  staging_id uuid NOT NULL REFERENCES advisor_distillate_records(staging_id),
  tenant_id text NOT NULL,
  review_type text NOT NULL,
  priority text NOT NULL DEFAULT 'medium',
  queue_status text NOT NULL DEFAULT 'pending',
  assigned_role text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (staging_id, review_type)
);

CREATE INDEX IF NOT EXISTS idx_distillate_records_tenant_table_status
  ON advisor_distillate_records (tenant_id, source_table, publication_status);
CREATE INDEX IF NOT EXISTS idx_distillate_review_queue_pending
  ON advisor_distillate_review_queue (tenant_id, queue_status, priority);
