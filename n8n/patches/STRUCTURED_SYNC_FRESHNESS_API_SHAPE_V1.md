# Structured sync safety P0 — future API contract (not wired yet)

Future endpoint shape for Core admin overview:

```
GET /v1/admin/sync-status?project_id=whieda
```

Response fragment:

```json
{
  "structured_sync": {
    "status": "healthy",
    "last_success_at": "2026-08-10T08:15:00+00:00",
    "age_minutes": 12.5,
    "last_failure_at": null,
    "last_failure_summary": null,
    "row_counts": {
      "rows_products": 120,
      "rows_aliases": 340
    },
    "healthy_threshold_minutes": 30
  }
}
```

Status values:

- `healthy` — last success <= 30 minutes ago
- `stale` — last success exists but older than 30 minutes
- `failed` — most recent terminal run is failed (even if last-good cache still serves)
- `never_synced` — no success and no runtime rows

Local probe today:

```powershell
python n8n/current/structured_sync_health_2026-08-10.py
```
