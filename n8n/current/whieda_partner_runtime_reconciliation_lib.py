"""WHIEDA partner runtime reconciliation (local/staging, dry-run by default)."""

from __future__ import annotations

import csv
import io
import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

TENANT_ID = "whieda"
REQUIRED_MASTER_COLUMNS = ("partner_id",)
DEFAULT_ALLOWLIST_PATH = Path(__file__).with_name("whieda_partner_runtime_allowlist.json")


class MasterSourceError(ValueError):
    """Malformed or empty Partners_Ref master source."""


@dataclass(frozen=True)
class AllowlistEntry:
    actor_id: str
    kind: str = ""
    reason: str = ""
    retain_roles: bool = True


@dataclass(frozen=True)
class AllowlistConfig:
    tenant_id: str
    platform_root_allowlist: tuple[AllowlistEntry, ...]
    schema_version: int = 1

    @property
    def actor_ids(self) -> frozenset[str]:
        return frozenset(entry.actor_id for entry in self.platform_root_allowlist)


@dataclass
class RuntimeActor:
    actor_id: str
    display_name: str
    active: bool
    telegram_username: str | None = None


@dataclass
class RuntimeProfile:
    ref_code: str
    owner_id: str
    enabled: bool
    display_mode: str = "named"


@dataclass
class RuntimeState:
    actors: dict[str, RuntimeActor] = field(default_factory=dict)
    profiles: list[RuntimeProfile] = field(default_factory=list)


@dataclass
class ActorDeactivationProposal:
    actor_id: str
    display_name: str
    reason: str = "absent_from_master_not_allowlisted"


@dataclass
class ProfileDeactivationProposal:
    ref_code: str
    owner_id: str
    display_name: str | None = None
    reason: str = "owner_actor_deactivation"


@dataclass
class ReconciliationPlan:
    mode: str
    tenant_id: str
    master_actor_ids: list[str]
    active_runtime_actors: list[dict[str, Any]]
    allowlisted_roots: list[dict[str, Any]]
    proposed_actor_deactivations: list[ActorDeactivationProposal]
    proposed_profile_deactivations: list[ProfileDeactivationProposal]
    master_upsert_sql: str
    deactivation_sql: str
    apply_sql: str
    would_delete: bool = False
    abort_reason: str | None = None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tenant_id": self.tenant_id,
            "would_delete": self.would_delete,
            "abort_reason": self.abort_reason,
            "master_actor_ids": self.master_actor_ids,
            "active_runtime_actors": self.active_runtime_actors,
            "allowlisted_roots": self.allowlisted_roots,
            "proposed_actor_deactivations": [
                {
                    "actor_id": item.actor_id,
                    "display_name": item.display_name,
                    "action": "set_active_false",
                    "reason": item.reason,
                }
                for item in self.proposed_actor_deactivations
            ],
            "proposed_profile_deactivations": [
                {
                    "ref_code": item.ref_code,
                    "owner_id": item.owner_id,
                    "action": "set_enabled_false",
                    "reason": item.reason,
                }
                for item in self.proposed_profile_deactivations
            ],
            "sql": {
                "master_upsert": self.master_upsert_sql.strip() or None,
                "deactivation": self.deactivation_sql.strip() or None,
                "apply_transaction": self.apply_sql.strip() or None,
            },
            "mutation_count": len(self.proposed_actor_deactivations) + len(self.proposed_profile_deactivations),
        }


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def sql_bool(value: str | bool | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if str(value or "").strip().lower() in {"true", "1", "yes", "y"} else "false"


def display_mode(page_mode: str) -> str:
    return "anonymous" if page_mode.strip().lower() in {"anonymous_ref", "anonymous"} else "named"


def parse_partners_ref_tsv(source: str | Path) -> list[dict[str, str]]:
    text = source if isinstance(source, str) else Path(source).read_text(encoding="utf-8")
    if not text.strip():
        raise MasterSourceError("Partners_Ref source is empty")
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows:
        raise MasterSourceError("Partners_Ref source has no data rows")
    headers = {key.strip() for key in rows[0].keys() if key}
    missing = [col for col in REQUIRED_MASTER_COLUMNS if col not in headers]
    if missing:
        raise MasterSourceError(f"Partners_Ref missing required columns: {', '.join(missing)}")
    return rows


def validate_master_rows(rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    if not rows:
        raise MasterSourceError("Partners_Ref has no partner rows")
    partner_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        partner_id = (row.get("partner_id") or "").strip()
        if not partner_id:
            continue
        if partner_id in seen:
            raise MasterSourceError(f"duplicate partner_id '{partner_id}' at row {index}")
        seen.add(partner_id)
        partner_rows.append(dict(row))
    if not partner_rows:
        raise MasterSourceError("Partners_Ref has no non-empty partner_id rows")
    return partner_rows


def master_actor_ids(rows: Sequence[Mapping[str, str]]) -> list[str]:
    return sorted({(row.get("partner_id") or "").strip() for row in rows if (row.get("partner_id") or "").strip()})


def load_allowlist_config(path: Path | None = None) -> AllowlistConfig:
    config_path = path or DEFAULT_ALLOWLIST_PATH
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    entries = tuple(
        AllowlistEntry(
            actor_id=str(item["actor_id"]).strip(),
            kind=str(item.get("kind") or "").strip(),
            reason=str(item.get("reason") or "").strip(),
            retain_roles=bool(item.get("retain_roles", True)),
        )
        for item in payload.get("platform_root_allowlist") or []
        if str(item.get("actor_id") or "").strip()
    )
    if not entries:
        raise ValueError(f"allowlist at {config_path} has no platform_root_allowlist entries")
    return AllowlistConfig(
        schema_version=int(payload.get("schema_version") or 1),
        tenant_id=str(payload.get("tenant_id") or TENANT_ID),
        platform_root_allowlist=entries,
    )


def build_master_upsert_sql(rows: Sequence[Mapping[str, str]], *, tenant_id: str = TENANT_ID) -> str:
    actor_values: list[str] = []
    role_values: list[str] = []
    profile_values: list[str] = []

    for row in rows:
        actor_id = (row.get("partner_id") or "").strip()
        if not actor_id:
            continue
        display_name = (row.get("display_name") or actor_id).strip()
        owner_actor_id = (row.get("owner_actor_id") or actor_id).strip()
        site_type = (row.get("site_type") or "").strip().lower()
        if owner_actor_id != actor_id or site_type == "platform_root":
            telegram_chat_id = None
            telegram_username = None
        else:
            telegram_username = (row.get("telegram_username") or "").strip().lstrip("@") or None
            telegram_chat_id = (row.get("telegram_chat_id") or "").strip() or None
        active = sql_bool(row.get("active"))
        actor_values.append(
            f"({sql_literal(actor_id)}, {sql_literal(tenant_id)}, {sql_literal(display_name)}, "
            f"{sql_literal(telegram_chat_id)}, {sql_literal(telegram_username)}, {active})"
        )

        ref_code = (row.get("ref_code") or "").strip()
        page_mode = (row.get("page_mode") or "standard_ref").strip()
        plan_status = (row.get("plan_status") or "").strip().lower()
        enabled = active == "true" and plan_status in {"", "active"}
        if ref_code:
            role_actor_id = owner_actor_id if site_type == "platform_root" else actor_id
            country = (row.get("country") or "GLOBAL").strip() or "GLOBAL"
            if enabled:
                role_values.append(
                    f"({sql_literal(tenant_id)}, {sql_literal(role_actor_id)}, 'referral_owner', "
                    f"{sql_literal(country)}, {sql_literal('')}, true)"
                )
            public_profile = {
                "partner_id": actor_id,
                "display_name": display_name,
                "page_mode": page_mode,
                "plan_code": (row.get("plan_code") or "").strip(),
                "plan_status": (row.get("plan_status") or "").strip(),
                "leads_access": (row.get("leads_access") or "").strip(),
                "public_site_url": (row.get("public_site_url") or row.get("ref_url") or "").strip(),
                "site_type": (row.get("site_type") or "").strip(),
                "focus_group": str(row.get("focus_group") or "").strip().upper() == "TRUE",
                "access_tier": (row.get("access_tier") or "").strip(),
                "watcher_actor_id": (row.get("watcher_actor_id") or "").strip(),
                "owner_actor_id": owner_actor_id,
            }
            owner_id = owner_actor_id
            country = (row.get("country") or "").strip() or None
            profile_values.append(
                "("
                f"{sql_literal(ref_code)}, {sql_literal(tenant_id)}, {sql_literal(owner_id)}, "
                f"{sql_literal(display_mode(page_mode))}, "
                f"{sql_literal(json.dumps(public_profile, ensure_ascii=False))}::jsonb, "
                f"{sql_literal(country)}, NULL, {sql_bool(enabled)}"
                ")"
            )

    if not actor_values:
        return ""

    sql = textwrap.dedent(
        f"""
        INSERT INTO lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username, active)
        VALUES
          {",\n  ".join(actor_values)}
        ON CONFLICT (actor_id) DO UPDATE
        SET display_name = excluded.display_name,
            telegram_chat_id = CASE
              WHEN excluded.telegram_chat_id IS NULL THEN lead_actors.telegram_chat_id
              WHEN EXISTS (
                SELECT 1 FROM lead_actors la
                WHERE la.tenant_id = excluded.tenant_id
                  AND la.telegram_chat_id = excluded.telegram_chat_id
                  AND la.actor_id <> excluded.actor_id
              ) THEN lead_actors.telegram_chat_id
              ELSE excluded.telegram_chat_id
            END,
            telegram_username = coalesce(excluded.telegram_username, lead_actors.telegram_username),
            active = excluded.active,
            updated_at = now();
        """
    )

    if role_values:
        sql += textwrap.dedent(
            f"""
            INSERT INTO lead_actor_roles (tenant_id, actor_id, role, country_code, region_code, active)
            VALUES
              {",\n  ".join(role_values)}
            ON CONFLICT (tenant_id, actor_id, role, country_code, region_code) DO UPDATE
            SET active = excluded.active;
            """
        )

    if profile_values:
        sql += textwrap.dedent(
            f"""
            INSERT INTO referral_profiles (
              ref_code, tenant_id, owner_id, display_mode, public_profile, country_code, region_code, enabled
            )
            VALUES
              {",\n  ".join(profile_values)}
            ON CONFLICT (ref_code) DO UPDATE
            SET owner_id = excluded.owner_id,
                display_mode = excluded.display_mode,
                public_profile = excluded.public_profile,
                country_code = excluded.country_code,
                enabled = excluded.enabled,
                profile_version = referral_profiles.profile_version + 1,
                updated_at = now();
            """
        )
    return sql.strip()


def propose_deactivations(
    runtime: RuntimeState,
    *,
    master_ids: Iterable[str],
    allowlist: AllowlistConfig,
) -> tuple[list[ActorDeactivationProposal], list[ProfileDeactivationProposal]]:
    master_set = set(master_ids)
    protected = master_set | allowlist.actor_ids
    actor_proposals: list[ActorDeactivationProposal] = []
    profile_proposals: list[ProfileDeactivationProposal] = []

    for actor in runtime.actors.values():
        if not actor.active:
            continue
        if actor.actor_id in protected:
            continue
        actor_proposals.append(
            ActorDeactivationProposal(actor_id=actor.actor_id, display_name=actor.display_name)
        )

    deactivate_actor_ids = {item.actor_id for item in actor_proposals}
    for profile in runtime.profiles:
        if not profile.enabled:
            continue
        if profile.owner_id in deactivate_actor_ids:
            owner = runtime.actors.get(profile.owner_id)
            profile_proposals.append(
                ProfileDeactivationProposal(
                    ref_code=profile.ref_code,
                    owner_id=profile.owner_id,
                    display_name=owner.display_name if owner else None,
                )
            )
    return actor_proposals, profile_proposals


def build_deactivation_sql(
    actor_proposals: Sequence[ActorDeactivationProposal],
    profile_proposals: Sequence[ProfileDeactivationProposal],
    *,
    tenant_id: str = TENANT_ID,
) -> str:
    chunks: list[str] = []
    if actor_proposals:
        actor_ids = ", ".join(sql_literal(item.actor_id) for item in actor_proposals)
        chunks.append(
            textwrap.dedent(
                f"""
                UPDATE lead_actors
                SET active = false,
                    updated_at = now()
                WHERE tenant_id = {sql_literal(tenant_id)}
                  AND actor_id IN ({actor_ids})
                  AND active = true;
                """
            ).strip()
        )
    if profile_proposals:
        ref_codes = ", ".join(sql_literal(item.ref_code) for item in profile_proposals)
        chunks.append(
            textwrap.dedent(
                f"""
                UPDATE referral_profiles
                SET enabled = false,
                    updated_at = now()
                WHERE tenant_id = {sql_literal(tenant_id)}
                  AND ref_code IN ({ref_codes})
                  AND enabled = true;
                """
            ).strip()
        )
    return "\n\n".join(chunks)


def build_apply_transaction(
    master_upsert_sql: str,
    deactivation_sql: str,
) -> str:
    parts = ["BEGIN;"]
    if master_upsert_sql.strip():
        parts.append(master_upsert_sql.strip())
    if deactivation_sql.strip():
        parts.append(deactivation_sql.strip())
    parts.append("COMMIT;")
    return "\n".join(parts)


def build_reconciliation_plan(
    master_rows: Sequence[Mapping[str, str]],
    runtime: RuntimeState,
    allowlist: AllowlistConfig,
    *,
    mode: str = "dry_run",
) -> ReconciliationPlan:
    validated = validate_master_rows(master_rows)
    ids = master_actor_ids(validated)
    actor_proposals, profile_proposals = propose_deactivations(
        runtime,
        master_ids=ids,
        allowlist=allowlist,
    )
    upsert_sql = build_master_upsert_sql(validated, tenant_id=allowlist.tenant_id)
    deactivation_sql = build_deactivation_sql(
        actor_proposals,
        profile_proposals,
        tenant_id=allowlist.tenant_id,
    )
    apply_sql = build_apply_transaction(upsert_sql, deactivation_sql)

    active_runtime = [
        {
            "actor_id": actor.actor_id,
            "display_name": actor.display_name,
            "active": actor.active,
            "in_master": actor.actor_id in ids,
            "allowlisted": actor.actor_id in allowlist.actor_ids,
        }
        for actor in sorted(runtime.actors.values(), key=lambda item: item.actor_id)
        if actor.active
    ]
    allowlisted = [
        {
            "actor_id": entry.actor_id,
            "kind": entry.kind,
            "reason": entry.reason,
            "retain_roles": entry.retain_roles,
        }
        for entry in allowlist.platform_root_allowlist
    ]

    return ReconciliationPlan(
        mode=mode,
        tenant_id=allowlist.tenant_id,
        master_actor_ids=ids,
        active_runtime_actors=active_runtime,
        allowlisted_roots=allowlisted,
        proposed_actor_deactivations=actor_proposals,
        proposed_profile_deactivations=profile_proposals,
        master_upsert_sql=upsert_sql,
        deactivation_sql=deactivation_sql,
        apply_sql=apply_sql,
        would_delete=False,
    )


def apply_plan_in_memory(
    master_rows: Sequence[Mapping[str, str]],
    runtime: RuntimeState,
    plan: ReconciliationPlan,
) -> RuntimeState:
    """Apply upsert + disable semantics to an in-memory runtime snapshot (tests)."""
    validated = validate_master_rows(master_rows)
    next_state = RuntimeState(
        actors={key: RuntimeActor(**vars(value)) for key, value in runtime.actors.items()},
        profiles=[RuntimeProfile(**vars(item)) for item in runtime.profiles],
    )

    for row in validated:
        actor_id = (row.get("partner_id") or "").strip()
        display_name = (row.get("display_name") or actor_id).strip()
        active = sql_bool(row.get("active")) == "true"
        existing = next_state.actors.get(actor_id)
        if existing:
            existing.display_name = display_name
            existing.active = active
        else:
            next_state.actors[actor_id] = RuntimeActor(
                actor_id=actor_id,
                display_name=display_name,
                active=active,
            )

        ref_code = (row.get("ref_code") or "").strip()
        if ref_code:
            owner_id = (row.get("owner_actor_id") or actor_id).strip()
            plan_status = (row.get("plan_status") or "").strip().lower()
            enabled = active and plan_status in {"", "active"}
            page_mode = (row.get("page_mode") or "standard_ref").strip()
            matched = next((p for p in next_state.profiles if p.ref_code == ref_code), None)
            if matched:
                matched.owner_id = owner_id
                matched.enabled = enabled
                matched.display_mode = display_mode(page_mode)
            else:
                next_state.profiles.append(
                    RuntimeProfile(
                        ref_code=ref_code,
                        owner_id=owner_id,
                        enabled=enabled,
                        display_mode=display_mode(page_mode),
                    )
                )

    for proposal in plan.proposed_actor_deactivations:
        actor = next_state.actors.get(proposal.actor_id)
        if actor:
            actor.active = False

    for proposal in plan.proposed_profile_deactivations:
        profile = next((p for p in next_state.profiles if p.ref_code == proposal.ref_code), None)
        if profile:
            profile.enabled = False

    return next_state


def runtime_state_from_rows(
    actors: Sequence[tuple[str, str, bool]],
    profiles: Sequence[tuple[str, str, bool]] | None = None,
) -> RuntimeState:
    state = RuntimeState(
        actors={
            actor_id: RuntimeActor(actor_id=actor_id, display_name=display_name, active=active)
            for actor_id, display_name, active in actors
        }
    )
    if profiles:
        state.profiles = [
            RuntimeProfile(ref_code=ref_code, owner_id=owner_id, enabled=enabled)
            for ref_code, owner_id, enabled in profiles
        ]
    return state
