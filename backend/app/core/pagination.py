"""Signed pagination cursors (LLD section 3.5).

cursor = b64url(payload) "." b64url(hmac_sha256(key, payload))
payload = {"k": last item key, "i": index name, "p": partition key it was issued for}
"""

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Sequence
from typing import Any

from app.core.errors import BadRequest


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CursorCodec:
    def __init__(self, keys: Sequence[bytes]) -> None:
        if not keys:
            raise ValueError("at least one cursor key is required")
        self._keys = list(keys)  # first key signs, all keys verify (rotation overlap)

    def _sign(self, key: bytes, payload: bytes) -> bytes:
        return hmac.new(key, payload, hashlib.sha256).digest()

    def encode(self, *, key: dict[str, Any], index: str | None, pk: str) -> str:
        payload = json.dumps({"k": key, "i": index, "p": pk}, separators=(",", ":")).encode()
        return f"{_b64e(payload)}.{_b64e(self._sign(self._keys[0], payload))}"

    def decode(self, cursor: str, *, pk: str, index: str | None) -> dict[str, Any]:
        try:
            body, sig = cursor.split(".", 1)
            payload, given = _b64d(body), _b64d(sig)
        except (ValueError, binascii.Error):
            raise BadRequest("Invalid cursor") from None
        if not any(hmac.compare_digest(self._sign(k, payload), given) for k in self._keys):
            raise BadRequest("Invalid cursor")
        try:
            data = json.loads(payload)
            key, idx, issued_pk = data["k"], data["i"], data["p"]
        except (ValueError, KeyError, TypeError):
            raise BadRequest("Invalid cursor") from None
        if issued_pk != pk or idx != index or not isinstance(key, dict):
            raise BadRequest("Invalid cursor")  # cross-org or cross-query reuse
        return key
