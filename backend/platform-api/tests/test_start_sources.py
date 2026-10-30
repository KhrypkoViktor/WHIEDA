"""Метка источника в ссылке в бота: /start src_<метка>[_<код>] (V42, 30.10.2026) — без базы."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.leads.start_sources import (
    SourceStart,
    parse_source_start_token,
    register_start_source,
    unknown_source_start,
)
from app.max.processor import handle_max_start
from app.max.update_parser import MaxEvent
from app.telegram.processor import process_core_telegram_update

SIX_PLATFORMS = ("vk", "ok", "dzen", "max", "youtube", "instagram")


# --- разбор метки ------------------------------------------------------------


@pytest.mark.parametrize("tag", SIX_PLATFORMS)
def test_six_platform_tags_parse(tag):
    assert parse_source_start_token(f"src_{tag}") == SourceStart(tag=tag, partner_part="")


def test_tag_with_partner_ref_code():
    assert parse_source_start_token("src_vk_anastasy") == SourceStart(tag="vk", partner_part="anastasy")


def test_tag_with_partner_invite_code_keeps_case_and_underscores():
    # Код приглашения чувствителен к регистру и может содержать «_» и «-»: делим только по первому «_».
    parsed = parse_source_start_token("src_dzen_aBcD-123_efGh")
    assert parsed == SourceStart(tag="dzen", partner_part="aBcD-123_efGh")


def test_tag_is_lowercased_but_prefix_case_is_free():
    assert parse_source_start_token("SRC_VK") == SourceStart(tag="vk", partner_part="")
    assert parse_source_start_token("  src_Instagram  ") == SourceStart(tag="instagram", partner_part="")


def test_tag_with_dash_and_digits():
    assert parse_source_start_token("src_yt-shorts2") == SourceStart(tag="yt-shorts2", partner_part="")


@pytest.mark.parametrize(
    "token",
    ["src_", "src__anastasy", "src_a" + "b" * 32, "src_в-к", "src_vk.ru", "src_vk!"],
)
def test_broken_tag_is_unknown_without_the_value(token):
    parsed = parse_source_start_token(token)
    assert parsed == unknown_source_start()
    assert parsed.tag == "unknown" and parsed.partner_part == ""


def test_tag_of_exactly_32_chars_is_kept():
    assert parse_source_start_token("src_" + "a" * 32).tag == "a" * 32


def test_broken_partner_part_is_dropped_but_tag_stays():
    assert parse_source_start_token("src_vk_ref!") == SourceStart(tag="vk", partner_part="")
    assert parse_source_start_token("src_vk_") == SourceStart(tag="vk", partner_part="")


@pytest.mark.parametrize("token", ["", "vk", "ref_abcdefgh", "site_abcdefgh", "shop_lending", "srcvk", "xsrc_vk"])
def test_not_a_source_token(token):
    assert parse_source_start_token(token) is None


def test_whole_start_param_fits_telegram_limit():
    # Метка до 32 знаков + код партнёра: «src_» (4) + 32 + «_» (1) + код ≤ 64 → код до 27 знаков не режется.
    token = "src_" + "a" * 32 + "_" + "b" * 27
    assert len(token) <= 64
    assert parse_source_start_token(token) == SourceStart(tag="a" * 32, partner_part="b" * 27)


# --- register_start_source: сбой записи не ломает старт ---------------------------


@pytest.mark.asyncio
async def test_register_never_raises_when_database_is_down():
    boom = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.leads.start_sources._known_before", boom), patch(
        "app.leads.start_sources.tenant_connection", side_effect=RuntimeError("db down")
    ):
        result = await register_start_source(
            "whieda", channel="telegram", channel_user_id=1, source=SourceStart(tag="vk"),
        )
    assert result.recorded is False
    # Не смогли проверить, знали ли человека, — новым его не считаем.
    assert result.first_seen is False


@pytest.mark.asyncio
async def test_register_rejects_unknown_channel():
    with pytest.raises(ValueError):
        await register_start_source("whieda", channel="vk", channel_user_id=1, source=SourceStart(tag="vk"))


# --- Telegram: маршрут /start --------------------------------------------------


def _start_update(text: str, *, chat_type: str = "private") -> dict:
    return {
        "message": {
            "text": text,
            "chat": {"id": 100, "type": chat_type},
            "from": {"id": 200, "username": "someone"},
        }
    }


def _patches():
    """Всё, что до и после /start ходит в базу или Telegram, — подменено; кабинет — «обычный /start»."""
    return (
        patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)),
        patch("app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)),
        patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)),
        patch("app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)),
        patch("app.telegram.processor.offer_marketing_consent", AsyncMock(return_value=False)),
        patch("app.telegram.processor._remove_legacy_reply_keyboard", AsyncMock(return_value=None)),
        patch("app.telegram.processor.deliver_text", AsyncMock()),
        patch("app.telegram.processor.send_telegram_text", AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", SIX_PLATFORMS)
async def test_start_with_tag_is_a_normal_start_and_records_the_tag(whieda_tenant, whieda_bot_binding, tag):
    register = AsyncMock()
    dashboard = AsyncMock(return_value={"ok": True, "route": "cabinet"})
    link_token = AsyncMock()
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], patch(
        "app.telegram.processor.register_start_source", register
    ), patch("app.telegram.processor.show_referral_dashboard", dashboard), patch(
        "app.telegram.processor.exchange_telegram_link_token", link_token
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update(f"/start src_{tag}"), "t-src", binding=whieda_bot_binding
        )
    assert result["route"] == "cabinet"  # то же, что у голого /start
    dashboard.assert_awaited_once()
    link_token.assert_not_called()  # метка не идёт в обмен токена связи с сайтом
    register.assert_awaited_once()
    kwargs = register.await_args.kwargs
    assert kwargs["channel"] == "telegram" and kwargs["channel_user_id"] == 200
    assert kwargs["source"] == SourceStart(tag=tag, partner_part="")


@pytest.mark.asyncio
async def test_start_with_tag_and_partner_passes_the_code_to_first_touch(whieda_tenant, whieda_bot_binding):
    register = AsyncMock()
    accept = AsyncMock()
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], patch(
        "app.telegram.processor.register_start_source", register
    ), patch("app.telegram.processor.accept_referral_start", accept), patch(
        "app.telegram.processor.show_referral_dashboard", AsyncMock(return_value={"ok": True, "route": "cabinet"})
    ):
        await process_core_telegram_update(
            whieda_tenant, _start_update("/start src_vk_proofinvite_code1"), "t-src-ref", binding=whieda_bot_binding
        )
        kwargs = register.await_args.kwargs
        assert kwargs["source"] == SourceStart(tag="vk", partner_part="proofinvite_code1")
        # accept_partner — тот же accept_referral_start, что у ref_<код>: первое касание, пригласивший не меняется.
        await kwargs["accept_partner"]("proofinvite_code1")
    accept.assert_awaited_once()
    assert accept.await_args.kwargs["invite_code"] == "proofinvite_code1"
    assert accept.await_args.kwargs["telegram_user_id"] == 200


@pytest.mark.asyncio
async def test_unknown_param_without_src_is_not_an_error(whieda_tenant, whieda_bot_binding):
    register = AsyncMock()
    dashboard = AsyncMock(return_value={"ok": True, "route": "cabinet"})
    not_found = HTTPException(status_code=404, detail={"ok": False, "error": "token_not_found"})
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], patch(
        "app.telegram.processor.exchange_telegram_link_token", AsyncMock(side_effect=not_found)
    ), patch("app.telegram.processor.register_start_source", register), patch(
        "app.telegram.processor.show_referral_dashboard", dashboard
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update("/start vk"), "t-unknown", binding=whieda_bot_binding
        )
    assert result["route"] == "cabinet"
    dashboard.assert_awaited_once()
    register.assert_awaited_once()
    source = register.await_args.kwargs["source"]
    assert source == unknown_source_start()
    assert "vk" not in repr(register.await_args)  # само значение параметра не хранится


@pytest.mark.asyncio
async def test_real_link_token_still_wins_over_unknown(whieda_tenant, whieda_bot_binding):
    from app.identity.service import LinkTokenExchangeResult

    fake = LinkTokenExchangeResult(
        link_id="l1", visitor_session_id="s1", first_ref=None, attributed_owner_id=None,
        mentor_display_name=None, journey_type="product", context={},
    )
    register = AsyncMock()
    dashboard = AsyncMock()
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as deliver, p[7], patch(
        "app.telegram.processor.exchange_telegram_link_token", AsyncMock(return_value=fake)
    ), patch("app.telegram.processor.register_start_source", register), patch(
        "app.telegram.processor.show_referral_dashboard", dashboard
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update("/start realtoken123"), "t-real", binding=whieda_bot_binding
        )
    assert result["route"] == "start_token"
    register.assert_not_called()
    dashboard.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [(409, "token_already_used"), (410, "token_expired")])
async def test_used_or_expired_link_token_keeps_old_behaviour(whieda_tenant, whieda_bot_binding, status, error):
    register = AsyncMock()
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], patch(
        "app.telegram.processor.exchange_telegram_link_token",
        AsyncMock(side_effect=HTTPException(status_code=status, detail={"ok": False, "error": error})),
    ), patch("app.telegram.processor.register_start_source", register):
        with pytest.raises(HTTPException):
            await process_core_telegram_update(
                whieda_tenant, _start_update("/start usedtoken1"), "t-used", binding=whieda_bot_binding
            )
    register.assert_not_called()


@pytest.mark.asyncio
async def test_known_prefixes_never_become_tags(whieda_tenant, whieda_bot_binding):
    register = AsyncMock()
    referral = AsyncMock(return_value={"ok": True, "route": "referral_start", "status": "attributed"})
    with patch("app.telegram.processor.register_start_source", register), patch(
        "app.telegram.processor.handle_referral_start_token", referral
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update("/start ref_aBcD-123_efGh"), "t-ref", binding=whieda_bot_binding
        )
    assert result["route"] == "referral_start"
    register.assert_not_called()


@pytest.mark.asyncio
async def test_tag_in_group_chat_is_not_recorded(whieda_tenant, whieda_bot_binding):
    register = AsyncMock()
    update = _start_update("/start@WHIEDA_Advisor_bot src_vk", chat_type="supergroup")
    update["message"]["chat"]["id"] = -100500
    with patch("app.telegram.processor.register_start_source", register):
        await process_core_telegram_update(whieda_tenant, update, "t-group", binding=whieda_bot_binding)
    register.assert_not_called()


# --- Max ---------------------------------------------------------------------


def _max_event(payload: str) -> MaxEvent:
    return MaxEvent(
        kind="start", user_id=9001, chat_id=777, text=payload, username=None, display_name="Max person", raw={}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [f"src_{tag}" for tag in SIX_PLATFORMS])
async def test_max_start_with_tag_welcomes_and_records(whieda_tenant, payload):
    register = AsyncMock()
    reply = AsyncMock()
    with patch("app.max.processor.register_start_source", register), patch("app.max.processor._reply", reply):
        result = await handle_max_start(whieda_tenant, _max_event(payload), "t-max")
    assert result["route"] == "max_start" and result["status"] == "source"
    reply.assert_awaited_once()
    kwargs = register.await_args.kwargs
    assert kwargs["channel"] == "max" and kwargs["channel_user_id"] == 9001
    assert kwargs["source"].tag == payload.removeprefix("src_")


@pytest.mark.asyncio
async def test_max_unknown_payload_is_recorded_as_unknown_not_dropped(whieda_tenant):
    register = AsyncMock()
    with patch("app.max.processor.register_start_source", register), patch("app.max.processor._reply", AsyncMock()):
        result = await handle_max_start(whieda_tenant, _max_event("something-else"), "t-max-unknown")
    assert result["status"] == "source" and result["tag"] == "unknown"
    assert register.await_args.kwargs["source"] == unknown_source_start()


@pytest.mark.asyncio
async def test_max_bare_start_and_ref_are_unchanged(whieda_tenant):
    register = AsyncMock()
    ensure = AsyncMock(return_value="max:whieda:9001")
    accept = AsyncMock()
    accept.return_value.status = "invalid"
    accept.return_value.inviter_actor_id = None
    with patch("app.max.processor.register_start_source", register), patch(
        "app.max.processor.ensure_channel_actor", ensure
    ), patch("app.max.processor.accept_channel_referral_start", accept), patch("app.max.processor._reply", AsyncMock()):
        plain = await handle_max_start(whieda_tenant, _max_event(""), "t-max-plain")
        referral = await handle_max_start(whieda_tenant, _max_event("ref_aBcD-123_efGh"), "t-max-ref")
    assert plain["status"] == "plain"
    assert referral["route"] == "max_start" and referral["status"] == "invalid"
    register.assert_not_called()
