-- Release 2 hardening: buffers are disposable; one transaction applies a run.
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS version_state text NOT NULL DEFAULT 'current';
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS superseded_by uuid;
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS source_medical_review_required boolean NOT NULL DEFAULT false;
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS source_marketing_allowed boolean NOT NULL DEFAULT false;
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS source_risk_level text;
ALTER TABLE advisor_testimonial_records ADD COLUMN IF NOT EXISTS source_medical_criticality text;

-- Release 2 routes confirmed source types explicitly. Keep legacy values readable,
-- but allow the clearer `consent` label for new review items.
ALTER TABLE advisor_testimonial_review_queue
  DROP CONSTRAINT IF EXISTS advisor_testimonial_review_queue_review_type_check;
ALTER TABLE advisor_testimonial_review_queue
  ADD CONSTRAINT advisor_testimonial_review_queue_review_type_check
  CHECK (review_type = ANY (ARRAY[
    'consent', 'consent_missing', 'medical', 'marketing', 'safety', 'negative', 'provenance'
  ]));

CREATE TABLE IF NOT EXISTS advisor_testimonial_hardening_buffer (
  import_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  tenant_id text NOT NULL,
  source_table text NOT NULL,
  external_record_id text NOT NULL,
  content_hash text NOT NULL,
  payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS advisor_testimonial_hardening_media_buffer (
  import_run_id uuid NOT NULL REFERENCES advisor_testimonial_import_runs(import_run_id),
  tenant_id text NOT NULL,
  resource_record_id text NOT NULL,
  payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_testimonial_current ON advisor_testimonial_records(tenant_id,source_table,external_record_id,version_state);
