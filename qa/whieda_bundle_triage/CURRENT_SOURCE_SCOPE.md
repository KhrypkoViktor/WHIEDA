# Current Bundle Source Scope

The bundle triage works on the current version of each candidate only.

Read-only runtime audit on 2026-08-28 found 25 `current` WHIEDA records and
26 `superseded` historical versions in `advisor_bundle_staging_records`.
Those 26 rows are history, not 26 missing candidates. They must not be merged
back into the owner-review queue unless a separate recovery task names a
specific record and version.

The offline fixture `09_BUNDLE_CANDIDATES.tsv` therefore contains the 25-row
current scope. The triage reports this scope explicitly and never claims a
source gap merely because historical versions exist.
