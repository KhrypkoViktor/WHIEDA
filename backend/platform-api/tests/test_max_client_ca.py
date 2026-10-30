"""Клиент Max: цепочка Минцифры только для API Max, адрес platform-api2.max.ru (30.10.2026)."""

from __future__ import annotations

import logging
import re
import ssl
from pathlib import Path
from typing import Any

import pytest

from app.max import client as max_client
from app.settings import Settings, get_settings

BUNDLE = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_ca_bundle.pem"


@pytest.fixture(autouse=True)
def _fresh_context_cache():
    max_client.max_ssl_context.cache_clear()
    yield
    max_client.max_ssl_context.cache_clear()


def _clean_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("PLATFORM_MAX_API_BASE", raising=False)
    monkeypatch.delenv("PLATFORM_MAX_CA_FILE", raising=False)
    return Settings(_env_file=None)


def test_defaults_point_to_api2_and_the_bundled_chain(monkeypatch):
    settings = _clean_settings(monkeypatch)
    assert settings.max_api_base == "https://platform-api2.max.ru"
    ca_file = Path(settings.max_ca_file)
    assert ca_file.name == "russian_trusted_ca_bundle.pem" and ca_file.parent.name == "certs"
    assert ca_file.resolve() == BUNDLE.resolve() and ca_file.is_file()


def test_env_overrides_base_and_ca_file(monkeypatch):
    monkeypatch.setenv("PLATFORM_MAX_API_BASE", "https://platform-api.max.ru")
    monkeypatch.setenv("PLATFORM_MAX_CA_FILE", "/etc/ssl/custom.pem")
    settings = Settings(_env_file=None)
    assert settings.max_api_base == "https://platform-api.max.ru"
    assert settings.max_ca_file == "/etc/ssl/custom.pem"


def test_bundle_holds_exactly_root_and_sub_ca():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(BUNDLE))
    names = sorted(
        dict(pair[0] for pair in cert["subject"])["commonName"] for cert in context.get_ca_certs()
    )
    assert names == ["Russian Trusted Root CA", "Russian Trusted Sub CA"]


def test_context_loads_bundle_when_file_exists(monkeypatch, tmp_path):
    # Пустой контекст вместо системного: в нём ровно то, что загрузил клиент.
    blank = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(max_client.ssl, "create_default_context", lambda *a, **k: blank)
    monkeypatch.setattr(get_settings(), "max_ca_file", str(BUNDLE))
    context = max_client.max_ssl_context()
    assert context is blank
    assert len(context.get_ca_certs()) == 2
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True


def test_context_without_file_warns_once_and_keeps_verification(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(get_settings(), "max_ca_file", str(tmp_path / "missing.pem"))
    with caplog.at_level(logging.WARNING, logger="whieda.max"):
        first = max_client.max_ssl_context()
        second = max_client.max_ssl_context()
    assert first is second
    assert isinstance(first, ssl.SSLContext)
    assert first.verify_mode == ssl.CERT_REQUIRED and first.check_hostname is True
    assert [r.getMessage() for r in caplog.records].count("max_ca_file_missing") == 1


def test_context_with_corrupt_file_falls_back_and_keeps_verification(monkeypatch, tmp_path, caplog):
    broken = tmp_path / "broken.pem"
    broken.write_text("-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(get_settings(), "max_ca_file", str(broken))
    with caplog.at_level(logging.WARNING, logger="whieda.max"):
        context = max_client.max_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert any(r.getMessage() == "max_ca_file_unreadable" for r in caplog.records)


class _Resp:
    status_code = 200
    text = "{}"

    def json(self) -> dict[str, Any]:
        return {"url": "https://upload.example/x", "token": "t", "urls": {}, "message": {"body": {"mid": "m1"}}}

    def raise_for_status(self) -> None:
        return None


def _install_fake_client(monkeypatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *a: Any, **k: Any) -> _Resp:
            return _Resp()

        get = delete = post

    monkeypatch.setattr(max_client.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(get_settings(), "max_bot_token", "test-token-not-real")
    return created


_CALLS = {
    "send_max_text": lambda: max_client.send_max_text(user_id=1, text="привет"),
    "send_max_message": lambda: max_client.send_max_message(chat_id=1, text="привет"),
    "upload_max_media": lambda: max_client.upload_max_media("video", b"data"),
    "get_chat": lambda: max_client.get_chat(1),
    "subscribe_webhook": lambda: max_client.subscribe_webhook("https://x.example/hook", secret=None),
    "get_me": lambda: max_client.get_me(),
    "get_max_video_url": lambda: max_client.get_max_video_url("tok"),
    "remove_max_member": lambda: max_client.remove_max_member(1, 2),
}


@pytest.mark.parametrize("name", sorted(_CALLS))
async def test_every_max_call_passes_the_chain_context_as_verify(monkeypatch, name):
    created = _install_fake_client(monkeypatch)
    monkeypatch.setattr(get_settings(), "max_ca_file", str(BUNDLE))
    await _CALLS[name]()
    assert created, f"{name} did not open an httpx client"
    expected = max_client.max_ssl_context()
    for kwargs in created:
        assert kwargs.get("verify") is expected


async def test_send_max_text_uses_api2_by_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_api_base", _clean_settings(monkeypatch).max_api_base)
    calls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, **k: Any) -> _Resp:
            calls.append(url)
            return _Resp()

    monkeypatch.setattr(max_client.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(get_settings(), "max_bot_token", "test-token-not-real")
    await max_client.send_max_text(user_id=1, text="привет")
    assert calls == ["https://platform-api2.max.ru/messages"]


def test_no_httpx_client_in_max_client_skips_verify():
    source = (Path(max_client.__file__)).read_text(encoding="utf-8")
    opened = re.findall(r"httpx\.AsyncClient\(([^)]*\)?)\)", source)
    assert len(opened) >= 8  # M1: 8 клиентов; M2 добавил answer_max_callback — все с verify
    assert all("verify=max_ssl_context()" in call for call in opened)
