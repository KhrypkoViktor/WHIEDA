# WHIEDA Runtime vs Master Audit - 2026-08-10

## Scope

Read-only comparison of the captured Google Sheets master snapshot with the external `advisor-dev-postgres` runtime cache. No runtime, n8n, Sheets, Telegram, or Dify data was changed.

Snapshot: `n8n/live-exports/structured-master/20260810T083328Z`.

## Result

The active runtime is current. Its latest successful structured sync finished at `2026-08-10 11:15:19 UTC` with status `success`.

## Safety deployment preflight

The live runtime does not yet have the new audit fields `sync_run_uuid`, `workflow_execution_id`, `error_summary`, or `error_metadata`. The live n8n workflow also has no Error Trigger attached. This is expected because Safety P0 is not deployed; it confirms that the migration and inactive Error Audit workflow are required parts of one controlled release.

| Layer | Master | Runtime | Result |
|---|---:|---:|---|
| Products | 40 | 40 | Match |
| Aliases | 120 rows / 118 unique pairs | 118 | Match after source deduplication |
| Resources | 239 | 239 | Match |
| Product cards | 23 | 23 | Match |
| Business FAQ | 13 | 13 | Match |
| Business objections | 23 | 23 | Match |
| Canonical questions | 20 | 20 | Match |
| Capability responses | 4 | 4 | Match |
| Clarification prompts | 7 | 7 | Match |
| Community resources | 1 | 1 | Match |
| Events | 1 | 1 | Match |
| Intent registry | 22 | 22 | Match |
| Product comparisons | 1 | 1 | Match |
| Product details | 0 | 0 | Match |
| Promotions | 9 | 9 | Match |
| Recommendation rules | 7 | 7 | Match |
| Starter basket templates | 6 | 6 | Match |
| Structure owners | 2 | 2 | Match |
| Users access | 8 | 8 | Match |

## Alias note

The two-row difference is not data loss. Master contains two duplicate alias/SKU pairs for the palantin; runtime stores the expected 118 unique pairs.

## Partner registry note

Master lists 6 partner entries; `lead_actors` has 8 active entries. Two non-master platform/system actors remain active. One owns an enabled referral profile; both have administrative or platform roles. This may be intentional, therefore no automatic disablement was performed.

Required follow-up: explicitly define platform-root/system actor allowlist and reconcile all other retired master actors to inactive without deleting history.

## Important correction

The local Postgres container beside n8n has an old partial cache. It is not the active WHIEDA runtime for this workflow. Live sync uses the external `advisor-dev-postgres` credential.
