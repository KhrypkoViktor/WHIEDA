"""`/start pro` — the PRO lock on the site sends people here to see the price and act.

The site menu shows a PRO badge to anyone without an active subscription; its
explainer card links to ``t.me/<bot>?start=pro``. A partner with a site gets
the renewal flow, a newcomer gets the site request flow — the same buttons the
referral dashboard already uses (``renew:start`` / ``site:create``).
"""

from __future__ import annotations

from typing import Any

from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

PRO_START_TOKEN = "pro"

_TEXT = (
    "PRO (сайт) — ваш партнёрский сайт, калькулятор, прайс повторной покупки, "
    "дизайн сайта и академия.\n"
    "Стоимость: 30 WWC$ (3 000 ₽) за 3 месяца.\n\n"
    "{action}"
)


def is_pro_start_token(token: str) -> bool:
    return str(token or "").strip().lower() == PRO_START_TOKEN


async def handle_pro_start(
    tenant: TenantContext, msg: TelegramMessage, trace_id: str
) -> dict[str, Any]:
    subscription = await resolve_partner_subscription_by_telegram_user_id(
        tenant.tenant_id, msg.user_id
    )
    if subscription:
        action = (
            "Нажмите «Продлить платформу» — бот покажет реквизиты, "
            "после оплаты пришлите чек сюда."
        )
        button = {"text": "Продлить платформу", "callback_data": "renew:start"}
    else:
        action = "Своего сайта у вас ещё нет. Нажмите «Создать свой сайт» — заявка уйдёт Виктору."
        button = {"text": "Создать свой сайт", "callback_data": "site:create"}
    await send_telegram_text(
        chat_id=str(msg.chat_id),
        text=_TEXT.format(action=action),
        bot_token=current_bot_binding().bot_token,
        reply_markup={"inline_keyboard": [[button]]},
    )
    return {"ok": True, "route": "pro_start", "has_site": bool(subscription), "trace_id": trace_id}
