# WHIEDA Advisor Acceptance Lab

Local acceptance gate for running the regression corpus against a **running Core API**.

## Quick start

```powershell
Copy-Item qa\acceptance\acceptance_target.example.json qa\acceptance\acceptance_target.local.json

# 1) Offline — corpus + target config only (no HTTP)
python qa\acceptance\run_acceptance.py --offline --target qa\acceptance\acceptance_target.local.json

# 2) Target contract — requires Core listening locally
python qa\acceptance\run_acceptance.py --check-target --target qa\acceptance\acceptance_target.local.json

# 3) Live run — full corpus (Core must pass check-target first)
python qa\acceptance\run_acceptance.py --run --target qa\acceptance\acceptance_target.local.json

# 4) All offline checks + pytest
.\qa\run_acceptance_all.ps1
```

## Target config

All routes and JSON field mappings live in `acceptance_target*.json` — **not hard-coded** in the runner.

Required advisor response extractions (via config paths):

- `answer_text`, `answer_mode`, product name/SKU
- photo / videos / PDF documents
- clarifications, `error_id`

## Outputs (gitignored)

| Path | Content |
|---|---|
| `qa/acceptance/reports/` | Markdown + JSON human/machine reports |
| `qa/acceptance/baselines/latest.json` | Normalized case results (no raw bodies) |
| `qa/acceptance/raw_responses/` | Full HTTP bodies (local only) |

Compare two runs:

```powershell
python qa\acceptance\compare_acceptance_runs.py qa\acceptance\reports\ACCEPTANCE_REPORT_<id>.json qa\acceptance\baselines\latest.json
```

## Status semantics

| Status | Meaning |
|---|---|
| **PASS** | Assertions satisfied |
| **FAIL** | HTTP error, timeout, SLA breach, or assertion miss |
| **SKIP** | `--dry-run` |
| **UNASSERTED** | Case lacks strict expectations — never auto-PASS |
| **NOT_RUN** | No live HTTP (dry-run / Core down) |

## Honest limits

- Offline mode does **not** validate advisor answers.
- Without Core on `base_url`, live status remains **NOT_RUN** / check-target **FAIL**.
- No writes to Sheets, Postgres, n8n, or Telegram.
