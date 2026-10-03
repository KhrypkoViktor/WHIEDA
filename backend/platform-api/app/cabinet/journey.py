"""Уровень кабинета (tier), замки (locks) и «Следующий шаг» — чистые функции.

Уровни (ТЗ §8): ``free`` — есть аккаунт в боте, оплаченного сайта нет;
``pro`` — сайт оплачен (активен или льготный срок); ``leader`` — PRO и право
лидера (пилот ``PLATFORM_LEADER_PILOT_TELEGRAM_IDS`` или строка
``partner_product_access.product_code = 'leader_cabinet'``).

Замки — словарь «раздел или действие → причина»; открытого ключа в нём нет:
``pro_required`` (нужен оплаченный сайт, кнопка «Подключить» → /start/),
``club_required``, ``leader_required``, ``crm_pilot_only`` (CRM пока у пилота),
``feature_disabled``, ``academy_not_open``.

Путь: шаги идут по порядку. Сделанный — ``done``; шаг с собственным замком —
``locked`` с причиной; первый несделанный без замка — ``current`` («Следующий
шаг»); остальные — ``upcoming`` (тоже ``locked``: «откроется по порядку»).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_LEADER = "leader"

MANUAL_STEPS: tuple[str, ...] = ("presentation", "invite_sent")

PRO_PATH: tuple[str, ...] = ("presentation", "profile", "lesson1", "invite_sent", "crm_contact", "club")
FREE_PATH: tuple[str, ...] = ("presentation", "free_lesson", "invite_sent", "want_site")

STEP_TITLES: dict[str, str] = {
    "presentation": "Посмотреть презентацию",
    "profile": "Заполнить профиль сайта",
    "lesson1": "Пройти урок 1 курса «Запуск WWC»",
    "invite_sent": "Отправить первое приглашение",
    "crm_contact": "Добавить первого человека в WWC CRM",
    "club": "Вступить в клуб",
    "free_lesson": "Пройти бесплатный урок",
    "want_site": "Подключить свой сайт",
}

# Куда ведёт кнопка шага (пути сайта; бот — отдельной ссылкой в links).
STEP_ACTIONS: dict[str, str] = {
    "presentation": "/start/",
    "profile": "#site",
    "lesson1": "/academy/",
    "invite_sent": "#partners",
    "crm_contact": "/crm/",
    "club": "/start/",
    "free_lesson": "/academy/",
    "want_site": "/start/",
}


def cabinet_tier(*, partner_paid: bool, leader_right: bool) -> str:
    if partner_paid and leader_right:
        return TIER_LEADER
    return TIER_PRO if partner_paid else TIER_FREE


def is_paid_tier(tier: str) -> bool:
    return tier in (TIER_PRO, TIER_LEADER)


def cabinet_locks(
    *,
    tier: str,
    has_site: bool,
    club_active: bool,
    crm_lock: str | None,
    academy_lock: str | None = None,
) -> dict[str, str]:
    """Закрытые разделы и действия. Партнёры, баланс, поддержка, настройки и
    «Что отправить» открыты всем — их ключей здесь не бывает."""
    paid = is_paid_tier(tier)
    locks: dict[str, str] = {}
    if not has_site:
        locks["site"] = "pro_required"
    if not paid:
        locks["profile"] = "pro_required"
        locks["calculator"] = "pro_required"
        locks["repeat_prices"] = "pro_required"
        locks["academy_pro"] = "pro_required"
    if academy_lock:
        locks["academy"] = academy_lock
    if crm_lock:
        locks["crm"] = crm_lock
    if not club_active:
        locks["club"] = "club_required"
    if tier != TIER_LEADER:
        locks["team"] = "leader_required"
    return locks


@dataclass(frozen=True)
class JourneyFacts:
    tier: str
    marks: frozenset[str]
    profile_complete: bool
    lesson1_done: bool
    invited_count: int
    crm_contacts: int | None  # None — CRM этому человеку закрыта
    crm_lock: str | None
    club_active: bool
    has_site: bool
    free_lesson_available: bool = False
    free_lesson_done: bool = False
    academy_lock: str | None = None


def _done(key: str, facts: JourneyFacts) -> bool:
    if key == "presentation":
        return "presentation" in facts.marks
    if key == "profile":
        return facts.profile_complete
    if key == "lesson1":
        return facts.lesson1_done
    if key == "invite_sent":
        return "invite_sent" in facts.marks or facts.invited_count > 0
    if key == "crm_contact":
        return bool(facts.crm_contacts)
    if key == "club":
        return facts.club_active
    if key == "free_lesson":
        return facts.free_lesson_done
    if key == "want_site":
        return facts.has_site
    return False


def _own_lock(key: str, facts: JourneyFacts) -> str | None:
    if key in ("lesson1", "free_lesson") and facts.academy_lock:
        return facts.academy_lock
    if key == "crm_contact" and facts.crm_contacts is None:
        return facts.crm_lock or "pro_required"
    if key == "club" and not facts.club_active:
        return "club_required"
    return None


def journey_steps(facts: JourneyFacts) -> dict[str, Any]:
    path = list(PRO_PATH if is_paid_tier(facts.tier) else FREE_PATH)
    if not is_paid_tier(facts.tier) and not facts.free_lesson_available:
        path.remove("free_lesson")  # бесплатного курса нет — шага тоже нет
    steps: list[dict[str, Any]] = []
    current: str | None = None
    for key in path:
        done = _done(key, facts)
        lock = None if done else _own_lock(key, facts)
        if done:
            status = "done"
        elif lock:
            status = "locked"
        elif current is None:
            status = "current"
            current = key
        else:
            status = "upcoming"
            lock = "previous_step"
        steps.append(
            {
                "key": key,
                "title": STEP_TITLES[key],
                "status": status,
                "done": done,
                "locked": status in ("locked", "upcoming"),
                "lock_reason": lock,
                "manual": key in MANUAL_STEPS,
                "action": STEP_ACTIONS[key],
            }
        )
    done_count = sum(1 for step in steps if step["done"])
    return {
        "path": "pro" if is_paid_tier(facts.tier) else "free",
        "steps": steps,
        "current": current,
        "done": done_count,
        "total": len(steps),
        "percent": round(100 * done_count / len(steps)) if steps else 0,
    }
