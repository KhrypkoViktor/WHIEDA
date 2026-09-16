"""Gemini sales in the support tunnel: «Оплачено» → who sold → deposit write-off
and the partner's WWC$; «Активировано до …»; the administrator's deposit
(«перевёл N» / «Получила»); «отчёт» / «баланс» / «тариф».

Everything the administrator sees is nameless: the client is a ticket
number, the partner is an e-mail login (owner, 15–16.09.2026).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.service_sales.service import (
    activate_sale,
    add_topup,
    confirm_topup,
    get_sale,
    get_sale_for_ticket,
    list_tariffs,
    month_report,
    partner_label,
    record_sale,
    set_tariff,
    split_sale,
    suggest_partner_for_client,
)
from app.settings import get_settings
from app.support.service import get_forum, get_ticket, set_forum_service_threads, ticket_label
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, create_forum_topic, send_telegram_text
from app.telegram.money import money, wwc
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

_CALLBACK_RE = re.compile(r"^(sale|dep):([a-z]+):([A-Za-z0-9_-]+)(?::([A-Za-z0-9_-]+))?(?::([A-Za-z0-9_-]+))?$")
_TOPUP_RE = re.compile(r"^(?:перев[её]л|пополнил|депозит)\s+(\d[\d\s]*)\s*(?:₽|руб\.?|р\.?)?$", re.IGNORECASE)
_TARIFF_RE = re.compile(r"^тариф(?:\s+([a-z0-9_]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+))?$", re.IGNORECASE)
_REPORT_WORDS = {"отчёт", "отчет", "/report"}
_BALANCE_WORDS = {"баланс", "депозит", "хвосты", "/deposit"}
SERVICE_COMMAND_TOKENS = {"перевёл", "перевел", "пополнил", "депозит", "баланс", "хвосты", "отчёт", "отчет", "тариф", "/report", "/deposit"}

OFFER_TITLES = {"gemini_6m": "Gemini Pro, 6 мес", "gemini_18m": "Gemini Pro, 18 мес"}
OFFER_MONTHS = {"gemini_6m": 6, "gemini_18m": 18}


# ----------------------------------------------------------------------------
# Who may act
# ----------------------------------------------------------------------------

def _admin_id() -> int | None:
    value = get_settings().platform_support_admin_telegram_id
    return int(value) if value else None


def _owner_id() -> int | None:
    value = get_settings().platform_billing_owner_telegram_id
    return int(value) if value else None


def is_service_operator(user_id: int) -> bool:
    """The owner and the service administrator run sales; nobody else."""
    return int(user_id) in {i for i in (_admin_id(), _owner_id()) if i is not None}


def is_service_command(text: str) -> bool:
    head = (str(text or "").strip().split() or [""])[0].lower()
    return head in SERVICE_COMMAND_TOKENS


async def _send(chat_id: int, text: str, *, reply_markup: dict | None = None, thread_id: int | None = None) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup, message_thread_id=thread_id,
    )


def _rub(minor: int) -> str:
    return money(int(minor), "RUB")


# ----------------------------------------------------------------------------
# Keyboards
# ----------------------------------------------------------------------------

def paid_button(ticket: dict[str, Any]) -> dict[str, Any]:
    return {"text": "Оплачено", "callback_data": f"sale:paid:{ticket['ticket_id']}"}


def _offer_keyboard(ticket_id: str) -> dict[str, Any]:
    rows = [[{"text": title, "callback_data": f"sale:offer:{ticket_id}:{code}"}] for code, title in OFFER_TITLES.items()]
    rows.append([{"text": "Отмена", "callback_data": f"sale:cancel:{ticket_id}"}])
    return {"inline_keyboard": rows}


def _activate_keyboard(sale: dict[str, Any]) -> dict[str, Any]:
    months = OFFER_MONTHS.get(str(sale["offer_code"]), 6)
    return {"inline_keyboard": [[{"text": f"Активировано на {months} мес", "callback_data": f"sale:until:{sale['sale_id']}:{months}"}]]}


# ----------------------------------------------------------------------------
# Callbacks
# ----------------------------------------------------------------------------

async def try_handle_service_sale_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    family, action, arg, extra, extra2 = match.groups()
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    if not is_service_operator(callback.user_id):
        return {"ok": False, "route": "service_sale", "status": "forbidden", "trace_id": trace_id}
    chat, thread = callback.chat_id, callback.thread_id

    if family == "dep" and action == "ok":
        if callback.user_id != _admin_id():
            await _send(chat, "Подтвердить получение может только администратор сервиса.", thread_id=thread)
            return {"ok": False, "route": "service_deposit", "status": "forbidden", "trace_id": trace_id}
        confirmed = await confirm_topup(tenant.tenant_id, entry_id=arg, confirmed_by=callback.user_id)
        if not confirmed:
            await _send(chat, "Это пополнение уже подтверждено.", thread_id=thread)
            return {"ok": True, "route": "service_deposit", "status": "already_confirmed", "trace_id": trace_id}
        text = f"Получено {_rub(confirmed['amount_minor'])}. Депозит: {_rub(confirmed['deposit_balance_minor'])}."
        await _send(chat, text, thread_id=thread)
        owner = _owner_id()
        if owner and owner != callback.user_id:
            await _send(owner, text)
        return {"ok": True, "route": "service_deposit", "status": "confirmed", "trace_id": trace_id}

    if action == "cancel":
        await _send(chat, "Отменено.", thread_id=thread)
        return {"ok": True, "route": "service_sale", "status": "cancelled", "trace_id": trace_id}

    if action == "paid":
        ticket = await get_ticket(tenant.tenant_id, ticket_id=arg)
        if not ticket:
            await _send(chat, "Заявка не найдена.", thread_id=thread)
            return {"ok": False, "route": "service_sale", "status": "no_ticket", "trace_id": trace_id}
        existing = await get_sale_for_ticket(tenant.tenant_id, ticket_id=arg)
        if existing:
            await _send(chat, f"Продажа по {ticket_label(ticket)} уже записана: {OFFER_TITLES.get(existing['offer_code'], existing['offer_code'])}.",
                        reply_markup=_activate_keyboard(existing) if existing["status"] == "paid" else None, thread_id=thread)
            return {"ok": True, "route": "service_sale", "status": "already_recorded", "trace_id": trace_id}
        await _send(chat, f"{ticket_label(ticket)}: что оплачено?", reply_markup=_offer_keyboard(arg), thread_id=thread)
        return {"ok": True, "route": "service_sale", "status": "offer_requested", "trace_id": trace_id}

    if action == "offer":
        ticket = await get_ticket(tenant.tenant_id, ticket_id=arg)
        if not ticket or not extra:
            return {"ok": False, "route": "service_sale", "status": "no_ticket", "trace_id": trace_id}
        offer = extra
        suggestion = await suggest_partner_for_client(tenant.tenant_id, client_telegram_user_id=int(ticket["user_telegram_user_id"]))
        rows = []
        if suggestion:
            label = await partner_label(tenant.tenant_id, suggestion["partner_ref"])
            rows.append([{"text": f"Партнёр: {label}", "callback_data": f"sale:seller:{arg}:{offer}:partner"}])
        rows.append([{"text": "Виктор (напрямую)", "callback_data": f"sale:seller:{arg}:{offer}:owner"}])
        rows.append([{"text": "Отмена", "callback_data": f"sale:cancel:{arg}"}])
        hint = "Клиента привёл партнёр — подтвердите, кто продал." if suggestion else "Партнёр у клиента не найден — продажа прямая."
        await _send(chat, f"{ticket_label(ticket)} · {OFFER_TITLES.get(offer, offer)}. {hint}", reply_markup={"inline_keyboard": rows}, thread_id=thread)
        return {"ok": True, "route": "service_sale", "status": "seller_requested", "trace_id": trace_id}

    if action == "seller":
        ticket = await get_ticket(tenant.tenant_id, ticket_id=arg)
        if not ticket or not extra or extra2 not in ("owner", "partner"):
            return {"ok": False, "route": "service_sale", "status": "no_ticket", "trace_id": trace_id}
        admin = _admin_id()
        if admin is None:
            await _send(chat, "Администратор сервиса не настроен.", thread_id=thread)
            return {"ok": False, "route": "service_sale", "status": "no_admin", "trace_id": trace_id}
        partner_ref = None
        if extra2 == "partner":
            suggestion = await suggest_partner_for_client(tenant.tenant_id, client_telegram_user_id=int(ticket["user_telegram_user_id"]))
            partner_ref = (suggestion or {}).get("partner_ref")
            if not partner_ref:
                await _send(chat, "Партнёр у клиента не найден — запишите как прямую продажу.", thread_id=thread)
                return {"ok": False, "route": "service_sale", "status": "no_partner", "trace_id": trace_id}
        sale = await record_sale(
            tenant.tenant_id, ticket=ticket, offer_code=extra, seller=extra2, partner_ref=partner_ref,
            admin_telegram_user_id=admin, created_by=callback.user_id,
        )
        if sale["idempotent"]:
            await _send(chat, f"Продажа по {ticket_label(ticket)} уже была записана.", thread_id=thread)
            return {"ok": True, "route": "service_sale", "status": "already_recorded", "trace_id": trace_id}
        summary = await _sale_summary(tenant.tenant_id, ticket, sale)
        await _send(chat, summary, reply_markup=_activate_keyboard(sale), thread_id=thread)
        await _notify_after_sale(tenant, ticket, sale, chat=chat, thread=thread)
        logger.info("service_sale_recorded", extra={"trace_id": trace_id, "ticket": ticket_label(ticket), "seller": extra2, "offer": extra})
        return {"ok": True, "route": "service_sale", "status": "recorded", "ticket": ticket_label(ticket), "trace_id": trace_id}

    if action == "until":
        sale = await get_sale(tenant.tenant_id, sale_id=arg)
        if not sale:
            return {"ok": False, "route": "service_sale", "status": "no_sale", "trace_id": trace_id}
        months = int(extra or OFFER_MONTHS.get(str(sale["offer_code"]), 6))
        until = _plus_months(datetime.now(timezone.utc), months)
        activated = await activate_sale(tenant.tenant_id, sale_id=arg, activated_until=until)
        ticket = await get_ticket(tenant.tenant_id, ticket_id=str(sale["ticket_id"]))
        if not activated:
            await _send(chat, "Лицензия уже отмечена как активированная.", thread_id=thread)
            return {"ok": True, "route": "service_sale", "status": "already_activated", "trace_id": trace_id}
        when = until.strftime("%d.%m.%Y")
        if ticket:
            await _send(int(ticket["user_chat_id"]), f"Лицензия {OFFER_TITLES.get(sale['offer_code'], sale['offer_code'])} активна до {when}. За неделю до окончания напомним.")
            from app.telegram import support as support_mod  # local import: support imports this module

            await support_mod._close_ticket_everywhere(tenant, str(ticket["ticket_id"]), ticket, reply_chat=chat, trace_id=trace_id)
        await _send(chat, f"Активировано до {when}.", thread_id=thread)
        return {"ok": True, "route": "service_sale", "status": "activated", "until": when, "trace_id": trace_id}

    return None


def _plus_months(start: datetime, months: int) -> datetime:
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return start.replace(year=year, month=month, day=day)


async def _sale_summary(tenant_id: str, ticket: dict[str, Any], sale: dict[str, Any]) -> str:
    who = "Виктор (напрямую)" if sale["seller"] == "owner" else f"партнёр {await partner_label(tenant_id, sale['partner_ref'])}"
    lines = [
        f"Продажа {ticket_label(ticket)} · {OFFER_TITLES.get(sale['offer_code'], sale['offer_code'])} · {_rub(sale['retail_minor'])}",
        f"Продал: {who}",
        f"Списано с депозита: {_rub(sale['owed_admin_minor'])}. Остаток депозита: {_rub(sale['deposit_balance_minor'])}",
    ]
    if sale["seller"] == "partner":
        lines.append(f"Партнёру: {wwc(int(sale['partner_share_wusd_minor']))}")
    lines.append(f"Виктору: {_rub(sale['owner_keeps_minor'])}")
    if int(sale["deposit_balance_minor"]) < int(sale["owed_admin_minor"]):
        lines.append("⚠️ Депозита на следующую лицензию не хватает — пополните («перевёл 20000»).")
    return "\n".join(lines)


async def _notify_after_sale(tenant: TenantContext, ticket: dict[str, Any], sale: dict[str, Any], *, chat: int, thread: int | None) -> None:
    bonus = sale.get("partner_bonus") or {}
    if bonus and bonus.get("telegram_chat_id") and not bonus.get("idempotent"):
        await _send(
            int(bonus["telegram_chat_id"]),
            "\n".join([
                f"+{wwc(int(bonus['amount_minor']))} за Gemini клиента {ticket_label(ticket)}.",
                f"Баланс: {wwc(int(bonus['balance_minor']))}.",
                "Личный кабинет: /cabinet",
            ]),
        )
    if bonus:
        label = await partner_label(tenant.tenant_id, sale["partner_ref"])
        await post_bonus_feed(tenant, f"{datetime.now(timezone.utc).strftime('%d.%m')} · {label} · +{wwc(int(bonus['amount_minor']))} · {ticket_label(ticket)} · {OFFER_TITLES.get(sale['offer_code'], sale['offer_code'])}")
    owner = _owner_id()
    if owner and owner != chat:
        await _send(owner, await _sale_summary(tenant.tenant_id, ticket, sale))


# ----------------------------------------------------------------------------
# Service topics in the forum: «Бонусы» and «Отчёты»
# ----------------------------------------------------------------------------

async def ensure_service_topics(tenant: TenantContext) -> dict[str, Any] | None:
    binding = current_bot_binding()
    forum = await get_forum(tenant.tenant_id, binding_id=binding.binding_id)
    if not forum:
        return None
    bonuses, reports = forum.get("bonuses_thread_id"), forum.get("reports_thread_id")
    if not bonuses:
        made = await create_forum_topic(chat_id=str(forum["chat_id"]), name="Бонусы", bot_token=binding.bot_token)
        bonuses = made.get("message_thread_id") if made.get("ok") else None
    if not reports:
        made = await create_forum_topic(chat_id=str(forum["chat_id"]), name="Отчёты", bot_token=binding.bot_token)
        reports = made.get("message_thread_id") if made.get("ok") else None
        if reports:
            await _send(int(forum["chat_id"]), "Команды здесь: «отчёт» — продажи и начисления за месяц, «баланс» — депозит и неподтверждённые пополнения, «перевёл 20000» — пополнение (Виктор), «тариф» — цены.", thread_id=int(reports))
    if bonuses != forum.get("bonuses_thread_id") or reports != forum.get("reports_thread_id"):
        await set_forum_service_threads(tenant.tenant_id, binding_id=binding.binding_id, bonuses_thread_id=bonuses, reports_thread_id=reports)
    return {**forum, "bonuses_thread_id": bonuses, "reports_thread_id": reports}


async def post_bonus_feed(tenant: TenantContext, line: str) -> None:
    forum = await ensure_service_topics(tenant)
    if forum and forum.get("bonuses_thread_id"):
        await _send(int(forum["chat_id"]), line, thread_id=int(forum["bonuses_thread_id"]))


def is_reports_topic(forum: dict[str, Any] | None, chat_id: int, thread_id: int | None) -> bool:
    return bool(forum and thread_id and forum.get("reports_thread_id") and int(forum["chat_id"]) == int(chat_id) and int(forum["reports_thread_id"]) == int(thread_id))


# ----------------------------------------------------------------------------
# Text commands: перевёл N · баланс · отчёт · тариф
# ----------------------------------------------------------------------------

async def try_handle_service_command(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    text = str(msg.text or "").strip()
    if not is_service_command(text) or not is_service_operator(msg.user_id):
        return None
    admin = _admin_id()
    chat, thread = msg.chat_id, msg.thread_id
    if admin is None:
        await _send(chat, "Администратор сервиса не настроен.", thread_id=thread)
        return {"ok": False, "route": "service_command", "status": "no_admin", "trace_id": trace_id}
    head = text.split()[0].lower()

    if head in _REPORT_WORDS:
        report = await month_report(tenant.tenant_id, admin_telegram_user_id=admin)
        await _send(chat, await _format_report(tenant.tenant_id, report), thread_id=thread)
        return {"ok": True, "route": "service_command", "status": "report", "trace_id": trace_id}

    if head in _BALANCE_WORDS:
        report = await month_report(tenant.tenant_id, admin_telegram_user_id=admin)
        lines = [f"Депозит у администратора: {_rub(report['deposit_balance_minor'])}."]
        for p in report["pending_topups"]:
            lines.append(f"Ожидает подтверждения: {_rub(p['amount_minor'])} от {p['created_at'].strftime('%d.%m')}")
        if report["low_balance"]:
            lines.append("⚠️ Меньше одной лицензии — пополните.")
        await _send(chat, "\n".join(lines), thread_id=thread)
        return {"ok": True, "route": "service_command", "status": "balance", "trace_id": trace_id}

    topup = _TOPUP_RE.fullmatch(text)
    if topup:
        if msg.user_id != _owner_id():
            await _send(chat, "Пополнение депозита отмечает Виктор.", thread_id=thread)
            return {"ok": False, "route": "service_command", "status": "forbidden", "trace_id": trace_id}
        amount = int(topup.group(1).replace(" ", "")) * 100
        entry = await add_topup(tenant.tenant_id, admin_telegram_user_id=admin, amount_minor=amount, sent_by=msg.user_id)
        keyboard = {"inline_keyboard": [[{"text": "Получила", "callback_data": f"dep:ok:{entry['entry_id']}"}]]}
        note = f"Пополнение депозита {_rub(amount)} от Виктора — подтвердите получение."
        forum = await ensure_service_topics(tenant)
        if forum and forum.get("reports_thread_id"):
            await _send(int(forum["chat_id"]), note, reply_markup=keyboard, thread_id=int(forum["reports_thread_id"]))
        else:
            await _send(admin, note, reply_markup=keyboard)
        if not (forum and forum.get("reports_thread_id") and int(forum["chat_id"]) == chat):
            await _send(chat, f"Записал перевод {_rub(amount)}; ждём подтверждения администратора.", thread_id=thread)
        return {"ok": True, "route": "service_command", "status": "topup_pending", "trace_id": trace_id}

    tariff = _TARIFF_RE.fullmatch(text)
    if tariff:
        if tariff.group(1) is None:
            rows = await list_tariffs(tenant.tenant_id)
            lines = ["Тарифы (цена · Карине при прямой · Карине при партнёрской · партнёру WWC$):"]
            lines += [f"{r['offer_code']}: {_rub(r['retail_minor'])} · {_rub(r['wholesale_direct_minor'])} · {_rub(r['wholesale_partner_minor'])} · {wwc(int(r['partner_share_wusd_minor']))}" for r in rows]
            lines.append("Изменить: тариф gemini_6m 3990 2990 2490 5")
            await _send(chat, "\n".join(lines), thread_id=thread)
            return {"ok": True, "route": "service_command", "status": "tariffs", "trace_id": trace_id}
        if msg.user_id != _owner_id():
            await _send(chat, "Тариф меняет Виктор.", thread_id=thread)
            return {"ok": False, "route": "service_command", "status": "forbidden", "trace_id": trace_id}
        code, retail, direct, partner, share = tariff.groups()
        if code not in OFFER_TITLES:
            await _send(chat, f"Неизвестный тариф. Есть: {', '.join(OFFER_TITLES)}.", thread_id=thread)
            return {"ok": False, "route": "service_command", "status": "unknown_offer", "trace_id": trace_id}
        row = await set_tariff(
            tenant.tenant_id, offer_code=code, retail_minor=int(retail) * 100, wholesale_direct_minor=int(direct) * 100,
            wholesale_partner_minor=int(partner) * 100, partner_share_wusd_minor=int(share) * 100, updated_by=msg.user_id,
        )
        direct_split, partner_split = split_sale(row, "owner"), split_sale(row, "partner")
        await _send(chat, f"{code}: клиент {_rub(row['retail_minor'])}; прямая — Карине {_rub(direct_split['owed_admin_minor'])}, Виктору {_rub(direct_split['owner_keeps_minor'])}; "
                          f"партнёрская — Карине {_rub(partner_split['owed_admin_minor'])}, партнёру {wwc(int(row['partner_share_wusd_minor']))}, Виктору {_rub(partner_split['owner_keeps_minor'])}.", thread_id=thread)
        return {"ok": True, "route": "service_command", "status": "tariff_set", "trace_id": trace_id}
    return None


async def _format_report(tenant_id: str, report: dict[str, Any]) -> str:
    lines = [
        f"Отчёт с {report['since'].strftime('%d.%m.%Y')}",
        f"Продаж: {report['sales']} на {_rub(report['retail_minor'])}",
        f"Списано с депозита: {_rub(report['owed_minor'])} · Виктору: {_rub(report['owner_minor'])}",
        f"Депозит сейчас: {_rub(report['deposit_balance_minor'])}" + (" ⚠️ меньше одной лицензии" if report["low_balance"] else ""),
    ]
    if report["pending_topups"]:
        lines.append("Не подтверждено: " + ", ".join(_rub(p["amount_minor"]) for p in report["pending_topups"]))
    if report["by_partner"]:
        lines.append("")
        lines.append("Начислено партнёрам:")
        for row in report["by_partner"]:
            lines.append(f"• {await partner_label(tenant_id, row['partner_ref'])} — {row['sales']} прод., {wwc(int(row['share_wusd_minor']))}")
    else:
        lines.append("Партнёрских продаж не было.")
    return "\n".join(lines)


__all__ = [
    "SERVICE_COMMAND_TOKENS", "ensure_service_topics", "is_reports_topic", "is_service_command",
    "is_service_operator", "paid_button", "post_bonus_feed",
    "try_handle_service_command", "try_handle_service_sale_callback",
]
