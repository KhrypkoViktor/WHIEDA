"""SQL к runtime-базе Core через n8n — без SSH.

Зачем отдельный модуль. Прежний путь (`apply_sql_via_n8n` в
run_partners_ref_runtime_sync_2026-08-01.py) после REST-активации временного
workflow ещё дёргает `ssh_run('n8n publish:workflow')`. Проверено 14.09.2026
на боевом workflow: REST `activate` сам делает версию активной
(`activeVersionId` == `versionId`), SSH-шаг лишний. А SSH-пароль в окружении
к тому же устарел, и весь путь падал на нём.

Плюс прежний путь не умел читать: Respond отдавал статичный `{"status":"ok"}`.
Здесь Respond возвращает строки узла Postgres — SELECT работает.

Надёжность (19.09.2026, «скрипты запускаются через раз»):
  * webhook временного workflow появляется не сразу — ждём до ~60 с, а не 30;
    сетевые обрывы повторяем, а не падаем;
  * файл с транзакцией и контрольным SELECT после COMMIT выполняется двумя
    вызовами: узел Postgres на многострочный текст возвращал пустой список, и
    `[]` выглядел как «ничего не сделано» — теперь строки SELECT приходят;
  * перед запуском удаляются зависшие `TEMP WWC SQL *` (если прошлый запуск
    оборвался) — десятки активных TEMP-workflow уже роняли n8n (см.
    WHIEDA_DEPLOY_HOSTS.md, 01.08.2026);
  * свой workflow всегда деактивируется и удаляется, даже при ошибке.

Использование из кода:
    from wwc_sql import run_sql
    rows = run_sql("select ref_code, owner_id from referral_profiles limit 5")

Из консоли:
    python wwc_sql.py "select count(*) from website_leads"
    python wwc_sql.py --file query.sql
"""
from __future__ import annotations

import importlib.util
import os
import json
import re
import sys
import time
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
TEMP_PREFIX = "TEMP WWC SQL "
WEBHOOK_WAIT_ATTEMPTS = 20   # × 3 с ≈ 60 с на появление webhook
WEBHOOK_WAIT_SLEEP = 3
NETWORK_RETRIES = 3


def _helper():
    spec = importlib.util.spec_from_file_location("whieda_sync", BASE / "publish_and_run_whieda_sync_2026-07-13.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


API_KEY = os.environ.get("WHIEDA_N8N_API_KEY", "")


def n8n_api(helper):
    """Public API n8n (/api/v1, заголовок X-N8N-API-KEY). Ключ — user-level env
    WHIEDA_N8N_API_KEY (создан владельцем 16.09.2026 при security-hardening),
    личный логин/пароль больше не нужны и не используются: он менялся и
    упирался в лимит 429."""
    if not API_KEY:
        raise RuntimeError("нет WHIEDA_N8N_API_KEY в окружении")
    session = requests.Session()
    session.headers["X-N8N-API-KEY"] = API_KEY
    session.verify = False
    return session, f"{helper.BASE_URL}/api/v1"


def split_statements(sql: str) -> list[str]:
    """Транзакция целиком + всё после последнего COMMIT отдельным вызовом.

    Узел Postgres в n8n на текст из нескольких команд отдаёт строки только
    первой; SELECT-проверка после COMMIT пропадала. Делим по последнему
    `COMMIT;` (регистр не важен); без COMMIT — один вызов, как раньше.
    """
    text = sql.strip()
    matches = list(re.finditer(r"\bCOMMIT\s*;", text, re.IGNORECASE))
    if not matches:
        return [text] if text else []
    cut = matches[-1].end()
    head, tail = text[:cut].strip(), text[cut:].strip()
    return [chunk for chunk in (head, tail) if chunk]


def _cleanup_stale(session: requests.Session, api: str) -> int:
    """Удалить чужие зависшие TEMP WWC SQL — они остаются, если прошлый запуск
    оборвали до finally. Активные TEMP-workflow грузят n8n."""
    removed = 0
    try:
        resp = session.get(f"{api}/workflows", params={"limit": 200}, timeout=30)
        resp.raise_for_status()
        for wf in resp.json().get("data", []):
            if not str(wf.get("name", "")).startswith(TEMP_PREFIX):
                continue
            wid = wf["id"]
            try:
                if wf.get("active"):
                    session.post(f"{api}/workflows/{wid}/deactivate", timeout=30)
                session.delete(f"{api}/workflows/{wid}", timeout=30)
                removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def _post_with_retries(url: str, payload: dict, timeout: int, on_empty=None) -> list[dict]:
    last = None
    network_failures = 0
    for attempt in range(WEBHOOK_WAIT_ATTEMPTS):
        if attempt:
            time.sleep(WEBHOOK_WAIT_SLEEP)
        try:
            resp = requests.post(url, json=payload, verify=False, timeout=timeout)
        except requests.RequestException as exc:  # обрыв сети / таймаут — повторяем
            network_failures += 1
            last = f"network: {exc}"
            if network_failures > NETWORK_RETRIES:
                break
            continue
        if resp.status_code == 404:  # webhook ещё не зарегистрирован
            last = f"404 on attempt {attempt + 1}"
            continue
        if resp.status_code >= 500:
            last = f"{resp.status_code}: {resp.text[:300]}"
            break
        resp.raise_for_status()
        if not resp.text.strip():
            # Узел Postgres упал: n8n отвечает 200 с пустым телом. Достаём текст
            # ошибки из выполнения — иначе «[]» выглядит как успех (19.09.2026).
            raise SqlExecutionError(on_empty() if on_empty else "пустой ответ n8n (запрос не выполнен)")
        return resp.json().get("rows", [])
    raise RuntimeError(f"webhook {url.rsplit('/', 1)[-1]}: {last}")


class SqlExecutionError(RuntimeError):
    """Postgres отверг запрос; текст — из журнала выполнения n8n."""


def _last_execution_error(session: requests.Session, api: str, workflow_id: str) -> str:
    try:
        ex = session.get(f"{api}/executions", params={"workflowId": workflow_id, "limit": 1, "includeData": "true"}, timeout=60).json()
        for e in ex.get("data", []):
            err = e.get("data", {}).get("resultData", {}).get("error", {}) or {}
            if err:
                return f"{err.get('message')} — {str(err.get('description') or '')[:300]}"
    except Exception as exc:  # журнал недоступен — хотя бы скажем, что запрос не прошёл
        return f"запрос не выполнен (журнал n8n недоступен: {exc})"
    return "запрос не выполнен (n8n не вернул строк)"


def run_sql(sql: str, *, timeout: int = 90) -> list[dict]:
    """Выполнить SQL; вернуть строки последнего запроса (SELECT после COMMIT — тоже)."""
    chunks = split_statements(sql)
    if not chunks:
        return []
    helper = _helper()
    session, api = n8n_api(helper)
    base = helper.BASE_URL
    _cleanup_stale(session, api)
    suffix = uuid.uuid4().hex[:10]
    path = f"wwc-sql-{suffix}"
    workflow = {
        "name": f"{TEMP_PREFIX}{suffix}",
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
             "id": "webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-200, 0]},
            {"parameters": {"jsCode": "const i=$input.first().json; const b=i.body&&typeof i.body==='object'?i.body:i; return [{json:b}];"},
             "id": "normalize", "name": "Code: payload", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [-20, 0]},
            {"parameters": {"operation": "executeQuery", "query": "={{ $json.sql }}", "options": {}},
             "id": "postgres", "name": "Postgres", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [180, 0],
             "credentials": {"postgres": helper.WORKFLOW_CREDENTIAL}},
            {"parameters": {"respondWith": "json",
                            "responseBody": "={{ JSON.stringify({ rows: $input.all().map(i => i.json) }) }}",
                            "options": {"responseCode": 200}},
             "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [380, 0]},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Code: payload", "type": "main", "index": 0}]]},
            "Code: payload": {"main": [[{"node": "Postgres", "type": "main", "index": 0}]]},
            "Postgres": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    try:
        payload = {k: workflow[k] for k in ("name", "nodes", "connections", "settings")}
        created = session.post(f"{api}/workflows", json=payload, timeout=60)
        created.raise_for_status()
        workflow_id = created.json()["id"]
        act = session.post(f"{api}/workflows/{workflow_id}/activate", timeout=60)
        act.raise_for_status()
        url = f"{base}/webhook/{path}"
        rows: list[dict] = []
        for chunk in chunks:
            rows = _post_with_retries(url, {"sql": chunk}, timeout, on_empty=lambda: _last_execution_error(session, api, workflow_id))
        return rows
    finally:
        if workflow_id:
            for action in ("deactivate", None):
                try:
                    if action:
                        session.post(f"{api}/workflows/{workflow_id}/{action}", timeout=30)
                    else:
                        session.delete(f"{api}/workflows/{workflow_id}", timeout=30)
                except Exception:
                    pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--file" in args:
        sql = Path(args[args.index("--file") + 1]).read_text(encoding="utf-8")
    else:
        sql = " ".join(args)
    result = run_sql(sql)
    if not result:
        print("ok: выполнено, строк в ответе нет", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
