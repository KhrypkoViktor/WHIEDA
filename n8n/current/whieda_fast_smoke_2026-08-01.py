"""Fast smoke subset: API + partners (no telegram/sync). Uses direct Supabase reads."""

from __future__ import annotations

import json
import uuid

import requests

from whieda_runtime_env import n8n_base_url, tls_verify
from whieda_runtime_pg_bootstrap import ensure_pgpassword
from whieda_runtime_read import query_rows

BASE_DIR = __file__ and __import__("pathlib").Path(__file__).resolve().parent
PUBLIC_API = f"{n8n_base_url()}/webhook/wwc-advisor-public-v1"

PARTNERS_QUERY = """
SELECT ref_code,
       public_profile->>'public_site_url' AS public_site_url,
       (public_profile->>'focus_group')::boolean AS focus_group,
       public_profile->>'access_tier' AS access_tier
FROM referral_profiles
WHERE tenant_id = 'whieda'
  AND ref_code IN ('ladnaya', 'mariam')
ORDER BY ref_code
"""


def main() -> None:
    results = []
    for case_id, payload in [
        ("api_price", {"question": "Сколько стоит активатор?", "session_id": "fast-q1", "ref": "ladnaya"}),
        ("api_greet", {"question": "Привет", "session_id": "fast-q2", "ref": "mariam"}),
    ]:
        r = requests.post(PUBLIC_API, json=payload, verify=tls_verify(), timeout=90)
        body = r.json() if r.text else {}
        ok = r.status_code == 200 and body.get("ok") is True and bool(body.get("answer_text"))
        results.append({"id": case_id, "pass": ok, "status": r.status_code, "route": body.get("route")})

    ensure_pgpassword()
    rows = query_rows(PARTNERS_QUERY)
    partner_ok = all(
        str(r.get("public_site_url", "")).startswith("https://")
        and r.get("focus_group") is True
        and r.get("access_tier")
        for r in rows
    )
    results.append({"id": "partners_runtime", "pass": partner_ok, "rows": rows})

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not all(item["pass"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
