-- Release 2 Testimonials: internal staging and review only.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS advisor_testimonial_import_runs (
  import_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  run_state text NOT NULL DEFAULT 'running',
  source_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
  error_text text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS advisor_testimonial_records (
  testimonial_staging_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  source_table text NOT NULL,
  external_record_id text NOT NULL,
  content_hash text NOT NULL,
  source_id text NOT NULL,
  source_locator text,
  raw_quote text,
  author_name text,
  author_role text,
  product_name text,
  situation text,
  use_description text,
  duration_text text,
  outcome_author_words text,
  neutral_summary text,
  source_format text,
  source_url text,
  source_timecode text,
  testimonial_class text NOT NULL CHECK (testimonial_class IN ('positive','neutral','negative','safety')),
  consent_state text NOT NULL DEFAULT 'unknown',
  publication_scope text NOT NULL DEFAULT 'internal_only',
  medical_review_state text NOT NULL DEFAULT 'review_required',
  marketing_review_state text NOT NULL DEFAULT 'review_required',
  publication_status text NOT NULL DEFAULT 'blocked_raw',
  duplicate_fingerprint text NOT NULL,
  first_seen_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  last_seen_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,source_table,external_record_id,content_hash)
);

CREATE TABLE IF NOT EXISTS advisor_testimonial_run_records (
  import_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  testimonial_staging_id uuid NOT NULL REFERENCES advisor_testimonial_records(testimonial_staging_id),
  action text NOT NULL CHECK(action IN ('inserted','reused','new_version','rejected')),
  PRIMARY KEY(import_run_id,testimonial_staging_id)
);

CREATE TABLE IF NOT EXISTS advisor_testimonial_links (
  link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_testimonial_staging_id uuid NOT NULL REFERENCES advisor_testimonial_records(testimonial_staging_id),
  to_testimonial_staging_id uuid NOT NULL REFERENCES advisor_testimonial_records(testimonial_staging_id),
  relation_type text NOT NULL CHECK(relation_type IN ('duplicate_of','same_product_context')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(from_testimonial_staging_id,to_testimonial_staging_id,relation_type)
);

CREATE TABLE IF NOT EXISTS advisor_testimonial_media_candidates (
  media_candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  tenant_id text NOT NULL,
  resource_record_id text NOT NULL,
  product_name text,
  media_type text,
  title text,
  resource_url text,
  source_id text NOT NULL,
  source_locator text,
  media_link_status text NOT NULL CHECK(media_link_status IN ('blocked_unconfirmed_testimonial','not_testimonial_media','linked_confirmed_testimonial')),
  linked_testimonial_staging_id uuid REFERENCES advisor_testimonial_records(testimonial_staging_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,resource_record_id)
);

CREATE TABLE IF NOT EXISTS advisor_testimonial_review_queue (
  review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  testimonial_staging_id uuid NOT NULL REFERENCES advisor_testimonial_records(testimonial_staging_id),
  review_type text NOT NULL CHECK(review_type IN ('consent_missing','medical','marketing','safety','negative','provenance')),
  queue_status text NOT NULL DEFAULT 'pending',
  last_evaluated_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(testimonial_staging_id,review_type)
);

CREATE INDEX IF NOT EXISTS idx_testimonial_records_class ON advisor_testimonial_records(testimonial_class, publication_scope);
