"""Academy notifications: which bot sends, what the texts say, the worker step."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.academy.notify import (
    author_inbox_url,
    lesson_page_url,
    notify_binding_id,
    reviewed_text,
    submitted_text,
)
from app.settings import get_settings


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("PLATFORM_ACADEMY_NOTIFY_BINDING", "PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "PLATFORM_ACADEMY_SITE_BASE"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_binding_explicit_then_scheduled_then_none(clean_env):
    assert notify_binding_id() is None
    clean_env.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot, other")
    get_settings.cache_clear()
    assert notify_binding_id() == "whieda-advisor-bot"
    clean_env.setenv("PLATFORM_ACADEMY_NOTIFY_BINDING", "wwc-cabinet-staging-bot")
    get_settings.cache_clear()
    assert notify_binding_id() == "wwc-cabinet-staging-bot"


def test_texts_and_links(clean_env):
    assert submitted_text("Мария", "Заливка", "Акварель") == "📝 Домашка от Мария — урок «Заливка»\nКурс: Акварель"
    assert submitted_text(None, "Заливка", "Акварель").startswith("📝 Домашка от ученика — урок")
    assert reviewed_text("accepted", "Заливка", "") == "✅ Домашка принята — урок «Заливка»."
    assert reviewed_text("returned", "Заливка", "Добавьте тени") == "↩️ Домашку вернули — урок «Заливка»:\nДобавьте тени"
    # Место — в query: фрагмент занимает вход с сайта (#wwc-login=…).
    assert author_inbox_url("abc") == "https://wwc.best/academy/author/?view=inbox&submission=abc"
    assert lesson_page_url("akvarel", "c") == "https://wwc.best/academy/?course=akvarel&lesson=c"


@pytest.mark.asyncio
async def test_staging_worker_sends_academy_notes_without_planning_crm(clean_env):
    from app.jobs import worker
    from app.telegram.bindings import BotBindingContext
    from app.tenancy import TenantContext

    clean_env.setenv("PLATFORM_ACADEMY_NOTIFY_BINDING", "wwc-cabinet-staging-bot")
    get_settings.cache_clear()
    staging = BotBindingContext(
        binding_id="wwc-cabinet-staging-bot",
        tenant=TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True}),
        bot_token_ref="env:X", webhook_secret_ref="env:Y", bot_username="staging_bot", status="active",
        processing_mode="core", bot_token="t", webhook_secret="s",
    )
    with patch.object(worker, "resolve_bot_binding_context", AsyncMock(return_value=staging)) as resolve, patch.object(
        worker, "process_due_notifications", AsyncMock(return_value=2)
    ) as send, patch("app.crm.digest.enqueue_crm_digests", AsyncMock()) as plan:
        assert await worker.scheduled_notifications_step(plan_crm=True) == {"due_sent": 2}
    resolve.assert_awaited_once_with("wwc-cabinet-staging-bot")
    plan.assert_not_awaited()
    assert send.await_args.args[0] == {"wwc-cabinet-staging-bot": staging}
