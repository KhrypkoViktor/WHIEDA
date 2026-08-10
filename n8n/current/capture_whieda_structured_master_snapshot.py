"""Create an immutable local snapshot of the WHIEDA Google Sheet master.

This script is read-only for Google Sheets. It downloads public TSV exports and
writes a dated snapshot plus a hash manifest under n8n/live-exports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests


SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
LAYERS = {
    "products": "1035748906",
    "aliases": "2001001",
    "resources": "2001005",
    "product_cards": "2001006",
    "product_details": "2001008",
    "product_comparisons": "1415928637",
    "users_access": "161104189",
    "structure_owners": "86214234",
    "business_objections": "43717728",
    "business_faq": "28151102",
    "promotions": "1621722235",
    "recommendation_rules": "1468974041",
    "starter_basket_templates": "1974396045",
    "events": "1444298798",
    "community_resources": "947236678",
    "intent_registry": "2001020",
    "clarification_prompts": "2001021",
    "capability_responses": "2001022",
    "canonical_questions": "1160261466",
    "partners_ref": "1733124410",
}


def nonempty_rows(payload: bytes) -> int:
    text = payload.decode("utf-8-sig", errors="replace")
    return sum(
        1
        for row in csv.DictReader(io.StringIO(text), delimiter="\t")
        if any(str(value or "").strip() for value in row.values())
    )


def fetch_layer(name: str, gid: str) -> tuple[str, str, bytes]:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={gid}"
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = requests.get(url, timeout=(10, 20))
            response.raise_for_status()
            return name, gid, response.content
        except requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError(f"Could not export {name} after 2 attempts") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture WHIEDA Sheet master snapshot")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_out = Path(__file__).resolve().parents[1] / "live-exports" / "structured-master" / stamp
    out_dir = args.out or default_out
    temp_dir = out_dir.with_name(out_dir.name + ".partial")
    temp_dir.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, object] = {
        "sheet_id": SHEET_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "layers": {},
    }
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_layer, name, gid) for name, gid in LAYERS.items()]
            downloaded = [future.result() for future in as_completed(futures)]
        for name, gid, payload in sorted(downloaded):
            destination = temp_dir / f"{name}.tsv"
            destination.write_bytes(payload)
            manifest["layers"][name] = {
                "gid": gid,
                "file": destination.name,
                "bytes": len(payload),
                "rows": nonempty_rows(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_dir.rename(out_dir)
        print(json.dumps({"snapshot": str(out_dir), "layers": len(LAYERS)}, ensure_ascii=False))
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
