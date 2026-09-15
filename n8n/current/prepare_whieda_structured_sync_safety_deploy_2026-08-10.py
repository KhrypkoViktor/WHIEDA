#!/usr/bin/env python3
"""Prepare or apply WHIEDA Structured Sync Safety P0 deployment (dry-run by default)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from whieda_structured_sync_safety_lib import (  # noqa: E402
    ERROR_WORKFLOW_ID_PLACEHOLDER,
    STRUCTURED_SYNC_WORKFLOW_ID,
)
from whieda_structured_sync_workflow_safety_p0 import (  # noqa: E402
    ERROR_WORKFLOW_NAME,
    WORKFLOW_NAME,
    build_error_audit_workflow,
    build_workflow_safety_p0,
)

PATCH_DIR = ROOT / "n8n" / "patches"
BASE_URL = os.environ.get("WHIEDA_N8N_BASE_URL", "https://sysarchn8n.duckdns.org")
LoginFactory = Callable[[], requests.Session]


class DeployApplyError(RuntimeError):
    """Apply failed; plan includes rollback state."""

    def __init__(self, message: str, plan: dict[str, Any]):
        super().__init__(message)
        self.plan = plan


def export_patch_artifacts(code: str) -> dict[str, Path]:
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    main_path = PATCH_DIR / "whieda_structured_sync_safety_p0_workflow.json"
    error_path = PATCH_DIR / "whieda_structured_sync_error_audit_workflow.json"
    main_path.write_text(
        json.dumps(build_workflow_safety_p0(code, active=False), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    error_path.write_text(
        json.dumps(build_error_audit_workflow(active=False), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"main": main_path, "error": error_path}


def build_dry_run_plan(code: str) -> dict[str, Any]:
    paths = export_patch_artifacts(code)
    main_artifact = json.loads(paths["main"].read_text(encoding="utf-8"))
    error_artifact = json.loads(paths["error"].read_text(encoding="utf-8"))
    return {
        "mode": "dry_run",
        "structured_sync_workflow_id": STRUCTURED_SYNC_WORKFLOW_ID,
        "artifacts": {key: str(path) for key, path in paths.items()},
        "artifact_checks": {
            "main_active": main_artifact.get("active"),
            "error_active": error_artifact.get("active"),
            "error_workflow_setting": main_artifact.get("settings", {}).get("errorWorkflow"),
        },
        "network": "not_used",
        "note": "Dry-run only. Re-run with --apply to perform n8n writes.",
    }


def login_session() -> requests.Session:
    email = os.environ.get("WHIEDA_N8N_EMAIL", "")
    password = os.environ.get("WHIEDA_N8N_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("WHIEDA_N8N_EMAIL and WHIEDA_N8N_PASSWORD required for --apply")
    session = requests.Session()
    response = session.post(
        f"{BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": email, "password": password},
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    return session


def find_workflow(session: requests.Session, name: str) -> dict[str, Any] | None:
    response = session.get(f"{BASE_URL}/rest/workflows?limit=200", verify=False, timeout=30)
    response.raise_for_status()
    for item in response.json().get("data", []):
        if item.get("name") == name:
            return item
    return None


def fetch_workflow(session: requests.Session, workflow_id: str) -> dict[str, Any]:
    response = session.get(f"{BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=30)
    response.raise_for_status()
    return response.json()["data"]


def save_workflow(session: requests.Session, payload: dict[str, Any]) -> dict[str, Any]:
    workflow_id = payload.get("id")
    if workflow_id:
        response = session.patch(
            f"{BASE_URL}/rest/workflows/{workflow_id}",
            json=payload,
            verify=False,
            timeout=60,
        )
    else:
        response = session.post(f"{BASE_URL}/rest/workflows", json=payload, verify=False, timeout=60)
    response.raise_for_status()
    body = response.json()
    return body.get("data", body)


def workflow_version_id(session: requests.Session, workflow_id: str, saved: dict[str, Any]) -> str:
    version_id = saved.get("versionId")
    if version_id:
        return str(version_id)
    refreshed = fetch_workflow(session, workflow_id)
    version_id = refreshed.get("versionId")
    if not version_id:
        raise RuntimeError(f"n8n did not return versionId for workflow {workflow_id}")
    return str(version_id)


def set_workflow_active(session: requests.Session, workflow_id: str, *, active: bool, version_id: str) -> None:
    endpoint = "activate" if active else "deactivate"
    response = session.post(
        f"{BASE_URL}/rest/workflows/{workflow_id}/{endpoint}",
        json={"versionId": version_id},
        verify=False,
        timeout=60,
    )
    response.raise_for_status()


def rollback_apply(
    session: requests.Session,
    *,
    backup_main: dict[str, Any] | None,
    backup_error: dict[str, Any] | None,
    error_workflow_id: str | None,
    error_created_this_run: bool,
    error_was_activated: bool,
) -> dict[str, Any]:
    state: dict[str, Any] = {"rolled_back": False, "errors": []}
    if backup_main and backup_main.get("id"):
        try:
            # n8n can merge nested settings on PATCH. Explicitly restore an absent
            # error workflow link as an empty value rather than relying on {}.
            restored_main = dict(backup_main)
            restored_settings = dict(backup_main.get("settings") or {})
            restored_settings.setdefault("errorWorkflow", "")
            restored_main["settings"] = restored_settings
            save_workflow(session, restored_main)
            version_id = workflow_version_id(session, str(restored_main["id"]), restored_main)
            if backup_main.get("active"):
                set_workflow_active(session, str(restored_main["id"]), active=True, version_id=version_id)
            state["main_restored_from_backup"] = True
            state["rolled_back"] = True
        except Exception as exc:  # pragma: no cover - best effort
            state["errors"].append(f"main_restore_failed: {exc}")

    if backup_error and backup_error.get("id"):
        try:
            save_workflow(session, backup_error)
            version_id = workflow_version_id(session, str(backup_error["id"]), backup_error)
            prior_active = bool(backup_error.get("active"))
            set_workflow_active(session, str(backup_error["id"]), active=prior_active, version_id=version_id)
            state["error_restored_from_backup"] = True
            state["error_restored_active"] = prior_active
            state["rolled_back"] = True
        except Exception as exc:  # pragma: no cover
            state["errors"].append(f"error_restore_failed: {exc}")
    elif error_workflow_id and error_created_this_run and error_was_activated:
        try:
            error_doc = fetch_workflow(session, error_workflow_id)
            version_id = workflow_version_id(session, error_workflow_id, error_doc)
            set_workflow_active(session, error_workflow_id, active=False, version_id=version_id)
            state["error_deactivated"] = True
        except Exception as exc:  # pragma: no cover
            state["errors"].append(f"error_deactivate_failed: {exc}")

    return state


def apply_deploy(
    code: str,
    session: requests.Session,
    *,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    backup_dir = backup_dir or (ROOT / "n8n" / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    plan: dict[str, Any] = {
        "mode": "apply",
        "structured_sync_workflow_id": STRUCTURED_SYNC_WORKFLOW_ID,
        "rollback": {"rolled_back": False},
    }

    existing_main = find_workflow(session, WORKFLOW_NAME)
    prior_main_active = bool(existing_main.get("active")) if existing_main else False
    plan["prior_main_state"] = {"active": prior_main_active, "workflow_id": (existing_main or {}).get("id")}

    existing_error = find_workflow(session, ERROR_WORKFLOW_NAME)
    prior_error_active = bool(existing_error.get("active")) if existing_error else False
    error_created_this_run = existing_error is None
    plan["prior_error_state"] = {
        "active": prior_error_active,
        "workflow_id": (existing_error or {}).get("id"),
        "created_this_run": error_created_this_run,
    }

    backup_main: dict[str, Any] | None = None
    backup_error: dict[str, Any] | None = None
    backup_path: Path | None = None
    error_backup_path: Path | None = None
    error_workflow_id: str | None = None
    main_workflow_id: str | None = None
    error_was_activated = False
    main_was_activated = False

    try:
        if existing_main:
            backup_main = fetch_workflow(session, str(existing_main["id"]))
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"whieda-structured-sync-before-safety-p0-{stamp}.json"
            backup_path.write_text(json.dumps(backup_main, ensure_ascii=False, indent=2), encoding="utf-8")
            plan["main_backup"] = str(backup_path)

        if existing_error:
            backup_error = fetch_workflow(session, str(existing_error["id"]))
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            error_backup_path = backup_dir / f"whieda-structured-sync-error-before-safety-p0-{stamp}.json"
            error_backup_path.write_text(json.dumps(backup_error, ensure_ascii=False, indent=2), encoding="utf-8")
            plan["error_backup"] = str(error_backup_path)

        error_payload = build_error_audit_workflow(active=False)
        if existing_error:
            error_payload["id"] = existing_error["id"]
        saved_error = save_workflow(session, error_payload)
        error_workflow_id = str(saved_error.get("id") or (existing_error or {}).get("id"))

        main_payload = build_workflow_safety_p0(code, active=False, error_workflow_id=error_workflow_id)
        if existing_main:
            main_payload["id"] = existing_main["id"]
        saved_main = save_workflow(session, main_payload)
        main_workflow_id = str(saved_main.get("id") or (existing_main or {}).get("id"))

        linked = fetch_workflow(session, main_workflow_id)
        linked_error_id = (linked.get("settings") or {}).get("errorWorkflow")
        if linked_error_id != error_workflow_id:
            raise RuntimeError(
                f"main workflow errorWorkflow mismatch: expected {error_workflow_id}, got {linked_error_id}"
            )

        if prior_main_active:
            error_version = workflow_version_id(session, error_workflow_id, saved_error)
            set_workflow_active(session, error_workflow_id, active=True, version_id=error_version)
            error_was_activated = True
            main_version = workflow_version_id(session, main_workflow_id, saved_main)
            set_workflow_active(session, main_workflow_id, active=True, version_id=main_version)
            main_was_activated = True

        plan.update(
            {
                "error_workflow_id": error_workflow_id,
                "main_workflow_id": main_workflow_id,
                "final_main_state": {"active": main_was_activated, "workflow_id": main_workflow_id},
                "final_error_state": {
                    "active": error_was_activated,
                    "workflow_id": error_workflow_id,
                    "created_this_run": error_created_this_run,
                },
                "activation_policy": (
                    "restored_prior_main_active_and_linked_error"
                    if prior_main_active
                    else "left_both_inactive"
                ),
            }
        )
        return plan
    except Exception as exc:
        plan["rollback"] = rollback_apply(
            session,
            backup_main=backup_main,
            backup_error=backup_error,
            error_workflow_id=error_workflow_id,
            error_created_this_run=error_created_this_run,
            error_was_activated=error_was_activated,
        )
        plan["final_main_state"] = plan.get("final_main_state", {"active": False, "workflow_id": main_workflow_id})
        plan["final_error_state"] = plan.get(
            "final_error_state",
            {
                "active": False,
                "workflow_id": error_workflow_id,
                "created_this_run": error_created_this_run,
            },
        )
        plan["error"] = str(exc)
        raise DeployApplyError(f"deploy apply failed: {exc}", plan) from exc


def build_deploy_plan(
    code: str,
    *,
    apply: bool,
    session: requests.Session | None = None,
    login_factory: LoginFactory | None = None,
) -> dict[str, Any]:
    if not apply:
        return build_dry_run_plan(code)
    if session is None:
        factory = login_factory or login_session
        session = factory()
    paths = export_patch_artifacts(code)
    plan = apply_deploy(code, session)
    plan["artifacts"] = {key: str(path) for key, path in paths.items()}
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy helper for Structured Sync Safety P0")
    parser.add_argument("--apply", action="store_true", help="Perform n8n writes (default is dry-run)")
    parser.add_argument(
        "--code-file",
        type=Path,
        default=CURRENT / "whieda_structured_sync_code_2026-07-13.js",
    )
    args = parser.parse_args()
    code = args.code_file.read_text(encoding="utf-8")
    try:
        plan = build_deploy_plan(code, apply=args.apply)
    except DeployApplyError as exc:
        print(json.dumps(exc.plan, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
