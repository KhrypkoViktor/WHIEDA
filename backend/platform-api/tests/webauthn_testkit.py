"""Программный аутентификатор WebAuthn для тестов (ключ доступа, 08.11.2026).

Это НЕ мок проверки подписи: тест строит настоящие ответы браузера (attestationObject «none» и подпись
ECDSA P-256 по authData + SHA-256(clientDataJSON)), а сервер проверяет их настоящей библиотекой webauthn.
Так же ведёт себя виртуальный аутентификатор браузера (Chrome DevTools WebAuthn.addVirtualAuthenticator).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

ORIGIN = "https://wwc.best"
RP_ID = "wwc.best"


class SoftAuthenticator:
    def __init__(self, *, origin: str = ORIGIN, rp_id: str = RP_ID) -> None:
        self.origin = origin
        self.rp_id = rp_id
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.user_handle: bytes | None = None

    # ---- помощники -------------------------------------------------------------------------------

    def _rp_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest()

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}
        ).encode()

    def _cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
        )

    # ---- navigator.credentials.create / get ------------------------------------------------------

    def create(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        """Ответ ``credential.toJSON()`` на PublicKeyCredentialCreationOptions (в JSON-виде библиотеки)."""
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        auth_data = (
            self._rp_hash()
            + bytes([0x45])  # UP | UV | AT
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options["challenge"], origin)),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal"],
            },
        }

    def get(
        self, options: dict[str, Any], *, origin: str | None = None, sign_count: int | None = None,
        private_key: ec.EllipticCurvePrivateKey | None = None,
    ) -> dict[str, Any]:
        """Ответ ``credential.toJSON()`` на PublicKeyCredentialRequestOptions; счётчик растёт сам."""
        if sign_count is None:
            self.sign_count += 1
            sign_count = self.sign_count
        auth_data = self._rp_hash() + bytes([0x05]) + sign_count.to_bytes(4, "big")  # UP | UV
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = (private_key or self.private_key).sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(self.user_handle) if self.user_handle else None,
            },
        }
