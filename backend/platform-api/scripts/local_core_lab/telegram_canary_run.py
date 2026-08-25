"""Live local Telegram canary cases against real Core webhook + Postgres + capture."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from local_core_lab.constants import DOCKER_CONTAINER, ROOT
from local_core_lab.telegram_canary_eval import evaluate_case_evidence, photo_then_text_ok
from local_core_lab.telegram_capture import TelegramCapture

CANARY_DB = "whieda_platform_telegram_canary"
API_BASE = "http://127.0.0.1:8080"
NORTH_BINDING = "north-canary-bot"
SOUTH_BINDING = "south-canary-bot"
DISABLED_BINDING = "north-disabled-bot"
NORTH_TOKEN = "north-token-local"
SOUTH_TOKEN = "south-token-local"
NORTH_SECRET = "north-secret-local"
SOUTH_SECRET = "south-secret-local"
SHARED_ALIAS = "общий тоник"
NORTH_ONLY_ALIAS = "северная смесь"
NORTH_CARD = "N-CARD"
SOUTH_CARD = "S-CARD"
NORTH_USD = "21.00 USD"
SOUTH_USD = "34.00 USD"
NORTH_PHOTO = "https://media.example.org/media/tenant-north/SHARE-01/north.webp"
SOUTH_PHOTO = "https://media.example.org/media/tenant-south/SHARE-01/south.webp"
NORTH_FAQ = "N-FAQ"
SOUTH_FAQ = "S-FAQ"
ONBOARDING_TEXT = "Раздел для новичков сейчас обновляется"
INSPECT_CLI = ROOT / "backend" / "platform-api" / "scripts" / "inspect_telegram_delivery_outbox.py"
SUPERUSER = "postgres"
SUPERPASSWORD = "local_staging_proof"


def _psql(sql: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={SUPERPASSWORD}",
            DOCKER_CONTAINER,
            "psql",
            "-U",
            SUPERUSER,
            "-d",
            CANARY_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "psql failed")[-500:])
    return proc.stdout.strip()


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 20.0,
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _webhook(
    binding_id: str,
    secret: str,
    update: dict[str, Any],
) -> int:
    body = json.dumps(update).encode("utf-8")
    status, _ = _http(
        "POST",
        f"{API_BASE}/v1/telegram/{binding_id}/webhook",
        headers={
            "Content-Type": "application/json",
            "Host": "127.0.0.1",
            "X-Telegram-Bot-Api-Secret-Token": secret,
        },
        data=body,
    )
    return status


def _message_update(update_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Lab"},
        },
    }


def _callback_update(update_id: int, chat_id: int, data: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": chat_id, "is_bot": False, "first_name": "Lab"},
            "message": {
                "message_id": update_id,
                "chat": {"id": chat_id, "type": "private"},
                "text": "menu",
            },
            "data": data,
        },
    }


def _wait_until(predicate, *, timeout_sec: float, interval: float = 0.4) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _inbox_count(binding_id: str, update_id: int | None = None) -> int:
    clause = f"binding_id = '{binding_id}'"
    if update_id is not None:
        clause += f" AND telegram_update_id = {int(update_id)}"
    return int(_psql(f"SELECT count(*) FROM telegram_update_inbox WHERE {clause};") or "0")


def _outbox_rows(binding_id: str, update_id: int | None = None) -> list[dict[str, Any]]:
    clause = f"binding_id = '{binding_id}'"
    if update_id is not None:
        clause += f" AND telegram_update_id = {int(update_id)}"
    raw = _psql(
        "SELECT coalesce(json_agg(row_to_json(t) ORDER BY sequence_no), '[]'::json) "
        "FROM (SELECT outbox_id, binding_id, tenant_id, telegram_update_id, sequence_no, "
        "kind, status, attempt_count, last_error_code, payload_json "
        f"FROM telegram_delivery_outbox WHERE {clause}) t;"
    )
    rows = json.loads(raw or "[]")
    return rows if isinstance(rows, list) else []


def _event_dicts(capture: TelegramCapture, *, after: int = 0) -> list[dict[str, Any]]:
    events = capture.snapshot()[after:]
    return [
        {
            "token": item.token,
            "bot_method": item.bot_method,
            "payload": item.payload,
            "status": item.status,
            "behavior": item.behavior,
        }
        for item in events
    ]


def _texts(events: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in events:
        payload = item.get("payload") or {}
        if payload.get("text"):
            texts.append(str(payload["text"]))
        if payload.get("photo"):
            texts.append(str(payload["photo"]))
    return texts


def _pass_case(case_id: str, **fields: Any) -> dict[str, Any]:
    payload = {"case_id": case_id, "status": "PASS", **fields}
    payload["status"] = evaluate_case_evidence(payload)
    return payload


def _fail_case(case_id: str, reason: str, **fields: Any) -> dict[str, Any]:
    payload = {"case_id": case_id, "status": "FAIL", "reason": reason, **fields}
    payload["status"] = "FAIL"
    return payload


def run_cases(capture: TelegramCapture, reports_dir: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cases.append(_case_north_shared(capture))
    cases.append(_case_south_shared(capture))
    cases.append(_case_north_only_from_south(capture))
    cases.append(_case_followup(capture))
    cases.append(_case_duplicate(capture))
    cases.append(_case_retry(capture))
    cases.append(_case_unknown_delivery(capture))
    cases.append(_case_dead(capture, reports_dir))
    cases.append(_case_photo_text_order(capture))
    cases.append(_case_unknown_binding(capture))
    cases.append(_case_onboarding_degraded(capture))
    return cases


def _case_north_shared(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L1_north_shared_alias"
    start = len(capture.snapshot())
    update_id = 11001
    status = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 501, SHARED_ALIAS))
    ok = _wait_until(
        lambda: any(
            item.bot_method == "sendMessage" and item.token == NORTH_TOKEN
            for item in capture.snapshot()[start:]
        ),
        timeout_sec=30,
    )
    events = _event_dicts(capture, after=start)
    texts = _texts(events)
    if status != 200 or not ok:
        return _fail_case(case_id, "webhook or capture timeout", http_status=status, capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        token=NORTH_TOKEN,
        require_capture=True,
        require_photo_then_text=True,
        capture=events,
        texts=texts,
        must_contain=[NORTH_CARD, NORTH_USD, NORTH_PHOTO],
        must_not_contain=[SOUTH_CARD, SOUTH_USD, SOUTH_PHOTO, SOUTH_FAQ],
        inbox_count=1,
        inbox_actual=_inbox_count(NORTH_BINDING, update_id),
        outbox_count=2,
        outbox_actual=len(_outbox_rows(NORTH_BINDING, update_id)),
        outbox_statuses=[row.get("status") for row in _outbox_rows(NORTH_BINDING, update_id)],
        outbox_status="sent",
    )


def _case_south_shared(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L2_south_shared_alias"
    start = len(capture.snapshot())
    update_id = 11002
    status = _webhook(SOUTH_BINDING, SOUTH_SECRET, _message_update(update_id, 502, SHARED_ALIAS))
    ok = _wait_until(
        lambda: any(
            item.bot_method == "sendMessage" and item.token == SOUTH_TOKEN
            for item in capture.snapshot()[start:]
        ),
        timeout_sec=30,
    )
    events = _event_dicts(capture, after=start)
    texts = _texts(events)
    if status != 200 or not ok:
        return _fail_case(case_id, "webhook or capture timeout", http_status=status, capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        token=SOUTH_TOKEN,
        require_capture=True,
        require_photo_then_text=True,
        capture=events,
        texts=texts,
        must_contain=[SOUTH_CARD, SOUTH_USD, SOUTH_PHOTO],
        must_not_contain=[NORTH_CARD, NORTH_USD, NORTH_PHOTO, NORTH_FAQ, "tenant-north"],
        inbox_count=1,
        inbox_actual=_inbox_count(SOUTH_BINDING, update_id),
        outbox_count=2,
        outbox_actual=len(_outbox_rows(SOUTH_BINDING, update_id)),
        outbox_statuses=[row.get("status") for row in _outbox_rows(SOUTH_BINDING, update_id)],
        outbox_status="sent",
    )


def _case_north_only_from_south(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L3_north_only_from_south"
    start = len(capture.snapshot())
    update_id = 11003
    status = _webhook(
        SOUTH_BINDING, SOUTH_SECRET, _message_update(update_id, 503, NORTH_ONLY_ALIAS)
    )
    ok = _wait_until(
        lambda: any(item.bot_method == "sendMessage" for item in capture.snapshot()[start:]),
        timeout_sec=30,
    )
    events = _event_dicts(capture, after=start)
    texts = _texts(events)
    joined = " ".join(texts)
    if status != 200 or not ok:
        return _fail_case(case_id, "webhook or capture timeout", http_status=status, capture=events)
    leaked = any(marker in joined for marker in (NORTH_CARD, "NX-CARD", NORTH_ONLY_ALIAS, "exclusive.webp"))
    return _pass_case(
        case_id,
        http_status=status,
        capture=events,
        texts=texts,
        require_capture=True,
        must_not_contain=[NORTH_CARD, "NX-CARD", "exclusive.webp"],
        inbox_count=1,
        inbox_actual=_inbox_count(SOUTH_BINDING, update_id),
        status="FAIL" if leaked else "PASS",
    )


def _case_followup(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L4_followup_price_photo"
    chat_id = 504
    start = len(capture.snapshot())
    first = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(11004, chat_id, SHARED_ALIAS))
    _wait_until(
        lambda: any(item.bot_method == "sendMessage" for item in capture.snapshot()[start:]),
        timeout_sec=30,
    )
    mid = len(capture.snapshot())
    second = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(11005, chat_id, "цена"))
    _wait_until(
        lambda: any(item.bot_method == "sendMessage" for item in capture.snapshot()[mid:]),
        timeout_sec=30,
    )
    later = len(capture.snapshot())
    third = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(11006, chat_id, "фото"))
    ok = _wait_until(
        lambda: any(item.bot_method == "sendPhoto" for item in capture.snapshot()[later:]),
        timeout_sec=30,
    )
    events = _event_dicts(capture, after=start)
    texts = _texts(events)
    if min(first, second, third) != 200 or not ok:
        return _fail_case(case_id, "follow-up timeout", capture=events, texts=texts)
    if NORTH_USD not in " ".join(texts) or NORTH_PHOTO not in " ".join(texts):
        return _fail_case(case_id, "follow-up lost tenant product", capture=events, texts=texts)
    if SOUTH_USD in " ".join(texts) or SOUTH_PHOTO in " ".join(texts):
        return _fail_case(case_id, "follow-up leaked south", capture=events, texts=texts)
    return _pass_case(
        case_id,
        http_status=third,
        token=NORTH_TOKEN,
        capture=events,
        texts=texts,
        require_capture=True,
        must_contain=[NORTH_USD, NORTH_PHOTO],
        must_not_contain=[SOUTH_USD, SOUTH_PHOTO],
    )


def _case_duplicate(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L5_duplicate_update"
    update_id = 11007
    start = len(capture.snapshot())
    first = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 505, SHARED_ALIAS))
    _wait_until(
        lambda: any(item.bot_method == "sendMessage" for item in capture.snapshot()[start:]),
        timeout_sec=30,
    )
    sent_before = len(
        [item for item in capture.snapshot()[start:] if item.bot_method in {"sendPhoto", "sendMessage"}]
    )
    second = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 505, SHARED_ALIAS))
    time.sleep(2)
    sent_after = len(
        [item for item in capture.snapshot()[start:] if item.bot_method in {"sendPhoto", "sendMessage"}]
    )
    inbox = _inbox_count(NORTH_BINDING, update_id)
    outbox = _outbox_rows(NORTH_BINDING, update_id)
    if first != 200 or second != 200 or inbox != 1 or len(outbox) != 2 or sent_after != sent_before:
        return _fail_case(
            case_id,
            "duplicate created extra inbox/outbox/capture",
            inbox_actual=inbox,
            outbox_actual=len(outbox),
            capture=_event_dicts(capture, after=start),
        )
    return _pass_case(
        case_id,
        http_status=second,
        capture=_event_dicts(capture, after=start),
        inbox_count=1,
        inbox_actual=inbox,
        outbox_count=2,
        outbox_actual=len(outbox),
    )


def _case_retry(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L6_confirmed_retry"
    capture.queue("fail_502")
    start = len(capture.snapshot())
    update_id = 11008
    status = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 506, SHARED_ALIAS))
    ok = _wait_until(
        lambda: all(row.get("status") == "sent" for row in _outbox_rows(NORTH_BINDING, update_id))
        and len(_outbox_rows(NORTH_BINDING, update_id)) >= 2,
        timeout_sec=40,
    )
    events = _event_dicts(capture, after=start)
    if status != 200 or not ok:
        return _fail_case(case_id, "retry did not reach sent", capture=events)
    failures = [item for item in events if item.get("status") == 502]
    if not failures:
        return _fail_case(case_id, "expected confirmed 502 then success", capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        token=NORTH_TOKEN,
        capture=events,
        texts=_texts(events),
        require_capture=True,
        require_photo_then_text=True,
        outbox_status="sent",
        outbox_statuses=[row.get("status") for row in _outbox_rows(NORTH_BINDING, update_id)],
    )


def _case_unknown_delivery(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L7_unknown_delivery"
    capture.queue("drop")
    start = len(capture.snapshot())
    update_id = 11009
    status = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 507, SHARED_ALIAS))
    ok = _wait_until(
        lambda: any(
            row.get("status") == "unknown_delivery" for row in _outbox_rows(NORTH_BINDING, update_id)
        ),
        timeout_sec=30,
    )
    time.sleep(3)
    rows = _outbox_rows(NORTH_BINDING, update_id)
    events = _event_dicts(capture, after=start)
    send_count = len(
        [item for item in events if item.get("bot_method") in {"sendPhoto", "sendMessage"}]
    )
    if status != 200 or not ok:
        return _fail_case(case_id, "unknown_delivery not recorded", capture=events, outbox_actual=len(rows))
    if send_count != 1:
        return _fail_case(case_id, "ambiguous send was retried", capture=events)
    if any(row.get("status") == "sent" for row in rows):
        return _fail_case(case_id, "unknown_delivery later marked sent", capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        capture=events,
        outbox_status="unknown_delivery",
        outbox_statuses=[row.get("status") for row in rows],
        outbox_count=2,
        outbox_actual=len(rows),
    )


def _case_dead(capture: TelegramCapture, reports_dir: Path) -> dict[str, Any]:
    case_id = "L8_terminal_dead"
    capture.set_default("fail_403")
    start = len(capture.snapshot())
    update_id = 11010
    status = _webhook(NORTH_BINDING, NORTH_SECRET, _message_update(update_id, 508, "правило склада"))
    ok = _wait_until(
        lambda: any(row.get("status") == "dead" for row in _outbox_rows(NORTH_BINDING, update_id)),
        timeout_sec=45,
    )
    capture.set_default("ok")
    rows = _outbox_rows(NORTH_BINDING, update_id)
    snapshot_path = reports_dir / "canary_dead_snapshot.json"
    reports_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    inspect = subprocess.run(
        [__import__("sys").executable, str(INSPECT_CLI), "--offline-snapshot", str(snapshot_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    events = _event_dicts(capture, after=start)
    if status != 200 or not ok or inspect.returncode != 0:
        return _fail_case(case_id, "dead inspect failed", capture=events, inspect=inspect.stdout[-500:])
    inspect_json = json.loads(inspect.stdout or "{}")
    if int((inspect_json.get("counts") or {}).get("dead") or 0) < 1:
        return _fail_case(case_id, "inspect did not show dead", capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        capture=events,
        outbox_status="dead",
        outbox_statuses=[row.get("status") for row in rows],
        inspect_dead=inspect_json["counts"]["dead"],
    )


def _case_photo_text_order(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L9_photo_then_text_order"
    start = len(capture.snapshot())
    update_id = 11011
    status = _webhook(SOUTH_BINDING, SOUTH_SECRET, _message_update(update_id, 509, SHARED_ALIAS))
    ok = _wait_until(
        lambda: photo_then_text_ok(_event_dicts(capture, after=start), token=SOUTH_TOKEN),
        timeout_sec=30,
    )
    events = _event_dicts(capture, after=start)
    if status != 200 or not ok:
        return _fail_case(case_id, "photo/text order missing", capture=events)
    caption = any(
        (item.get("payload") or {}).get("caption")
        for item in events
        if item.get("bot_method") == "sendPhoto"
    )
    if caption:
        return _fail_case(case_id, "photo had caption", capture=events)
    return _pass_case(
        case_id,
        http_status=status,
        token=SOUTH_TOKEN,
        capture=events,
        require_capture=True,
        require_photo_then_text=True,
        texts=_texts(events),
        must_contain=[SOUTH_PHOTO],
    )


def _case_unknown_binding(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L11_unknown_disabled_bad_secret"
    start = len(capture.snapshot())
    unknown_status = _webhook("no-such-binding", NORTH_SECRET, _message_update(11012, 510, SHARED_ALIAS))
    disabled_status = _webhook(
        DISABLED_BINDING, NORTH_SECRET, _message_update(11013, 511, SHARED_ALIAS)
    )
    bad_secret = _webhook(NORTH_BINDING, "wrong-secret", _message_update(11014, 512, SHARED_ALIAS))
    time.sleep(1)
    unknown_inbox = int(
        _psql("SELECT count(*) FROM telegram_update_inbox WHERE binding_id = 'no-such-binding';") or "0"
    )
    disabled_inbox = _inbox_count(DISABLED_BINDING)
    extra_events = [
        item
        for item in capture.snapshot()[start:]
        if item.bot_method in {"sendPhoto", "sendMessage"}
    ]
    if unknown_status != 200 or disabled_status != 200 or bad_secret != 403:
        return _fail_case(
            case_id,
            f"unexpected HTTP {unknown_status}/{disabled_status}/{bad_secret}",
            capture=_event_dicts(capture, after=start),
        )
    if unknown_inbox or disabled_inbox or extra_events:
        return _fail_case(
            case_id,
            "unknown/disabled created inbox or outbound",
            inbox_actual=unknown_inbox + disabled_inbox,
            capture=_event_dicts(capture, after=start),
        )
    return _pass_case(
        case_id,
        http_status=bad_secret,
        capture=[],
        inbox_count=0,
        inbox_actual=unknown_inbox + disabled_inbox,
        outbox_count=0,
        outbox_actual=len(extra_events),
    )


def _case_onboarding_degraded(capture: TelegramCapture) -> dict[str, Any]:
    case_id = "L10_onboarding_feature_readiness"
    _psql("DROP TABLE IF EXISTS onboarding_enrollments CASCADE;")
    start = len(capture.snapshot())
    status = _webhook(
        NORTH_BINDING, NORTH_SECRET, _message_update(11015, 513, "начать обучение")
    )
    ok = _wait_until(
        lambda: any(
            ONBOARDING_TEXT in str((item.payload or {}).get("text") or "")
            for item in capture.snapshot()[start:]
        ),
        timeout_sec=20,
    )
    mid = len(capture.snapshot())
    callback_status = _webhook(
        NORTH_BINDING, NORTH_SECRET, _callback_update(11016, 513, "nav:menu")
    )
    _wait_until(
        lambda: any(item.bot_method == "sendMessage" for item in capture.snapshot()[mid:]),
        timeout_sec=20,
    )
    events = _event_dicts(capture, after=start)
    texts = _texts(events)
    joined = " ".join(texts)
    false_success = any(marker in joined.lower() for marker in ("день 1", "обучение начато", "вы записаны"))
    if status != 200 or callback_status != 200 or not ok:
        return _fail_case(case_id, "controlled degradation missing", capture=events, texts=texts)
    if false_success or ONBOARDING_TEXT not in joined:
        return _fail_case(case_id, "callback looked like onboarding success", capture=events, texts=texts)
    return _pass_case(
        case_id,
        http_status=status,
        capture=events,
        texts=texts,
        require_capture=True,
        must_contain=[ONBOARDING_TEXT],
    )
