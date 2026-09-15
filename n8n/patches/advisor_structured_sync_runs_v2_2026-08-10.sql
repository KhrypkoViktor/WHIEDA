-- Local/staging migration for structured sync safety P0 audit journal.
-- Safe to run repeatedly (IF NOT EXISTS / guarded alters).

CREATE TABLE IF NOT EXISTS advisor_structured_sync_runs (
  run_id bigserial PRIMARY KEY,
  sync_run_uuid text,
  project_id text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text NOT NULL,
  workflow_execution_id text,
  rows_products integer NOT NULL DEFAULT 0,
  rows_aliases integer NOT NULL DEFAULT 0,
  rows_resources integer NOT NULL DEFAULT 0,
  rows_product_cards integer NOT NULL DEFAULT 0,
  rows_product_details integer NOT NULL DEFAULT 0,
  rows_product_comparisons integer NOT NULL DEFAULT 0,
  rows_users_access integer NOT NULL DEFAULT 0,
  rows_structure_owners integer NOT NULL DEFAULT 0,
  rows_business_objections integer NOT NULL DEFAULT 0,
  rows_business_faq integer NOT NULL DEFAULT 0,
  rows_promotions integer NOT NULL DEFAULT 0,
  rows_recommendation_rules integer NOT NULL DEFAULT 0,
  rows_starter_basket_templates integer NOT NULL DEFAULT 0,
  rows_events integer NOT NULL DEFAULT 0,
  rows_community_resources integer NOT NULL DEFAULT 0,
  rows_intent_registry integer NOT NULL DEFAULT 0,
  rows_clarification_prompts integer NOT NULL DEFAULT 0,
  rows_capability_responses integer NOT NULL DEFAULT 0,
  rows_canonical_questions integer NOT NULL DEFAULT 0,
  rows_partners_ref integer NOT NULL DEFAULT 0,
  error_summary text
);

ALTER TABLE advisor_structured_sync_runs
  ALTER COLUMN finished_at DROP NOT NULL;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS sync_run_uuid text;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS workflow_execution_id text;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_business_objections integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_business_faq integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_promotions integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_recommendation_rules integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_starter_basket_templates integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_events integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_community_resources integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_intent_registry integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_clarification_prompts integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_capability_responses integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_canonical_questions integer NOT NULL DEFAULT 0;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS rows_partners_ref integer NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'advisor_structured_sync_runs' AND column_name = 'error_text'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'advisor_structured_sync_runs' AND column_name = 'error_summary'
  ) THEN
    ALTER TABLE advisor_structured_sync_runs RENAME COLUMN error_text TO error_summary;
  END IF;
END $$;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS error_summary text;

ALTER TABLE advisor_structured_sync_runs
  ADD COLUMN IF NOT EXISTS error_metadata jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS advisor_structured_sync_runs_uuid_uq
  ON advisor_structured_sync_runs (sync_run_uuid)
  WHERE sync_run_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS advisor_structured_sync_runs_project_started_idx
  ON advisor_structured_sync_runs (project_id, started_at DESC);
