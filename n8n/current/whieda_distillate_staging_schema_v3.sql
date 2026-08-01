-- WHIEDA Release 0.1 hardening. Isolated staging only; no runtime tables.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE advisor_distillate_import_runs
  ADD COLUMN IF NOT EXISTS run_state text NOT NULL DEFAULT 'completed',
  ADD COLUMN IF NOT EXISTS error_text text,
  ADD COLUMN IF NOT EXISTS finished_at timestamptz;

ALTER TABLE advisor_distillate_records
  ADD COLUMN IF NOT EXISTS first_seen_run_id uuid,
  ADD COLUMN IF NOT EXISTS last_seen_run_id uuid;

ALTER TABLE advisor_distillate_candidates
  ADD COLUMN IF NOT EXISTS last_evaluated_run_id uuid,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE advisor_distillate_review_queue
  ADD COLUMN IF NOT EXISTS last_evaluated_run_id uuid;

CREATE TABLE IF NOT EXISTS advisor_distillate_run_records (
  import_run_id uuid NOT NULL REFERENCES advisor_distillate_import_runs(import_run_id),
  staging_id uuid NOT NULL REFERENCES advisor_distillate_records(staging_id),
  action text NOT NULL CHECK (action IN ('inserted','reused','new_version','rejected')),
  source_table text NOT NULL,
  external_record_id text NOT NULL,
  content_hash text NOT NULL,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (import_run_id, staging_id)
);

CREATE TABLE IF NOT EXISTS advisor_distillate_candidate_audit (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id uuid NOT NULL REFERENCES advisor_distillate_candidates(candidate_id),
  import_run_id uuid NOT NULL REFERENCES advisor_distillate_import_runs(import_run_id),
  candidate_status text NOT NULL,
  proposed_intent_id text,
  reason text,
  conflict_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(candidate_id, import_run_id)
);

CREATE TABLE IF NOT EXISTS advisor_distillate_review_buffer (
  import_run_id uuid NOT NULL,
  staging_id uuid NOT NULL,
  tenant_id text NOT NULL,
  review_type text NOT NULL,
  priority text NOT NULL DEFAULT 'medium'
);

CREATE INDEX IF NOT EXISTS idx_distillate_run_records_action
  ON advisor_distillate_run_records(import_run_id, action);
CREATE INDEX IF NOT EXISTS idx_distillate_review_queue_pending
  ON advisor_distillate_review_queue(queue_status, review_type);
