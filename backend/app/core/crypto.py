"""KMS envelope field encryption (LLD section 3.7).

One data key per value (AES-256-GCM). Stored: {ct, nonce, wrapped_key, key_id}.
The encryption context binds the ciphertext to org, subject and field.
Key rotation: new values use the current CMK. Old values stay decryptable because the
wrapped key records which key wrapped it (KMS resolves it); re-encrypt lazily with `rewrap`.
"""

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class FieldEncryptor:
    def __init__(self, kms: Any, key_id: str) -> None:
        self._kms, self._key_id = kms, key_id

    def encrypt(self, plaintext: str, context: dict[str, str]) -> dict[str, str]:
        dk = self._kms.generate_data_key(
            KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=context
        )
        nonce = os.urandom(12)
        ct = AESGCM(dk["Plaintext"]).encrypt(nonce, plaintext.encode(), self._aad(context))
        return {
            "ct": _b64(ct),
            "nonce": _b64(nonce),
            "wrapped_key": _b64(dk["CiphertextBlob"]),
            "key_id": self._key_id,
        }

    def decrypt(self, blob: dict[str, str], context: dict[str, str]) -> str:
        key = self._kms.decrypt(
            CiphertextBlob=base64.b64decode(blob["wrapped_key"]), EncryptionContext=context
        )["Plaintext"]
        pt = AESGCM(key).decrypt(
            base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), self._aad(context)
        )
        return pt.decode()

    def rewrap(self, blob: dict[str, str], context: dict[str, str]) -> dict[str, str]:
        return self.encrypt(self.decrypt(blob, context), context)

    @staticmethod
    def _aad(context: dict[str, str]) -> bytes:
        return "|".join(f"{k}={context[k]}" for k in sorted(context)).encode()


def mask(value: str, visible: int = 4) -> str:
    return "*" * max(len(value) - visible, 0) + value[-visible:]


class LocalFieldEncryptor(FieldEncryptor):
    """Local development stand-in for KMS: a fixed in-process key wrapper. Never used in dev, staging or prod."""

    def __init__(self, secret: bytes) -> None:
        import hashlib

        self._root = hashlib.sha256(secret).digest()
        super().__init__(None, "local")

    def encrypt(self, plaintext: str, context: dict[str, str]) -> dict[str, str]:
        nonce = os.urandom(12)
        ct = AESGCM(self._root).encrypt(nonce, plaintext.encode(), self._aad(context))
        return {"ct": _b64(ct), "nonce": _b64(nonce), "wrapped_key": "", "key_id": "local"}

    def decrypt(self, blob: dict[str, str], context: dict[str, str]) -> str:
        pt = AESGCM(self._root).decrypt(
            base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), self._aad(context)
        )
        return pt.decode()
