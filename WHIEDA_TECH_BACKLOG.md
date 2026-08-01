# WHIEDA Technical Backlog

## P0: Release 2 Testimonials hardening

- Make the testimonials import atomic across records, lineage, review queue, media candidates, duplicate links, and final run state. A failure after any phase must leave no rows introduced by that failed run.
- Add `--simulate-failure-phase` tests after records/lineage, review queue, media candidates, and duplicate links.
- Add idempotency tests: same corpus twice, one changed source row, interrupted run, and successful rerun after failure.
- Do not call all `37` resource rows media candidates. Report separately:
  - confirmed testimonial media;
  - unconfirmed testimonial media;
  - unrelated resources.
- Report source composition separately from classification:
  - testimonials;
  - usage patterns;
  - safety signals;
  - resource links.
- Add validation for duplicate external IDs, required provenance, required testimonial content, allowed values, and malformed URLs.
- Generate immutable per-run JSON/Markdown reports containing `run_id`; keep a separate explicit latest pointer if needed.
- Add active-version/superseded semantics so review items belonging to an old content version do not remain indistinguishable from current work.

## Before NSP onboarding

- Remove hardcoded `TENANT = "whieda"` from distillate importers.
- Move Supabase connection values out of source code into a secure runtime configuration / credential provider.
- Create the first safe Git baseline before further releases. Exclude private access maps, credentials, generated reports/backups, `.env`, and `node_modules`; do not commit secrets.
