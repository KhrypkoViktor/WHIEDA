"""Per-tenant wording and feature profile, owned by the tenant team.

`app/advisor/voice.py` keeps the WHIEDA (home) wording and a neutral default;
a tenant that wants its own greeting, fallback menu, medical boundary or
contact handle registers a profile here. Unknown tenants get `None` and keep
the neutral voice.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TenantProfile:
    tenant_id: str
    display_name: str
    contact_handle: str | None = None
    calculator: bool = False
    partner_prices: bool = False
    greeting: str = ""
    capabilities: str = ""
    help_text: str = ""
    fallback_menu: str = ""
    medical_boundary: str = ""
    unknown_product: str = ""
    gap_overrides: dict[str, str] = field(default_factory=dict)

    def service_text(self, intent_id: str) -> str | None:
        mapping = {
            "greeting": self.greeting,
            "capabilities": self.capabilities,
            "help": self.help_text,
        }
        text = mapping.get(intent_id, "")
        return text or None

    def gap_text(self, kind: str) -> str | None:
        if kind in self.gap_overrides:
            return self.gap_overrides[kind]
        if kind == "medical_or_safety_boundary":
            return self.medical_boundary or None
        if kind == "unknown_product":
            return self.unknown_product or self.fallback_menu or None
        if kind in {"unrouted_message", "unknown_followup", "unsupported_topic"}:
            return self.fallback_menu or None
        return None


_REGISTRY: dict[str, TenantProfile] = {}


def register_tenant_profile(profile: TenantProfile) -> TenantProfile:
    _REGISTRY[profile.tenant_id] = profile
    return profile


def get_tenant_profile(tenant_id: str | None) -> TenantProfile | None:
    if not tenant_id:
        return None
    return _REGISTRY.get(str(tenant_id).strip())


def registered_tenant_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


from app.tenants import nsp as _nsp  # noqa: E402,F401  (registers nsp-maxim)
