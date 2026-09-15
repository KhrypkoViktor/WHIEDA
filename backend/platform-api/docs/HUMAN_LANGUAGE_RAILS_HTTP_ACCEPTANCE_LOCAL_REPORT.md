# Human Language Rails HTTP Acceptance — Local Report

Mode: local HTTP acceptance against Platform Core only. No Core changes in this task.

- Run ID: `20260814T191033Z-9ded1af4`
- Status: **PASS**
- Phase: `full_accepted`

## Execution totals

- Selected accepted: 183
- Executed: 175
- PASS: 175
- FAIL: 0
- NOT_RUN_SETUP: 8
- NOT_RUN_PENDING (corpus): 40

## Failures by rail

- `direct_answer`: {'PASS': 53, 'NOT_RUN_SETUP': 7}
- `product_choices`: {'PASS': 33}
- `task_selection`: {'PASS': 42}
- `universal_menu`: {'NOT_RUN_SETUP': 1, 'PASS': 47}

## Live failures (case id + rail)

- none in this run (or HTTP lab not executed)

## Reproduce

```bash
python qa/human_language_rails/run_human_language_rails.py --dry-run
python qa/human_language_rails/run_human_language_rails.py --live
python backend/platform-api/scripts/run_local_core_lab.py --e2e --human-language-rails --golden-master-seed
```
