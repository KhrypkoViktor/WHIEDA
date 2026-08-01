-- Run once for the Release 0.1 upgrade. Staging-only cleanup of duplicate
-- candidates left by interrupted pre-hardening runs.
DELETE FROM advisor_distillate_candidates older
USING advisor_distillate_candidates newer
WHERE older.staging_id = newer.staging_id
  AND older.candidate_type = newer.candidate_type
  AND older.candidate_id < newer.candidate_id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_distillate_candidates_staging_type
  ON advisor_distillate_candidates(staging_id, candidate_type);
