import json
import logging

import boto3
import pytest
from app.core.config import Settings
from app.core.crypto import FieldEncryptor, mask
from app.core.logging import JsonFormatter, redact
from moto import mock_aws
from pydantic import ValidationError

BASE = {
    "COGNITO_POOL_ID": "ap-southeast-1_X",
    "COGNITO_CLIENT_IDS": "a, b",
    "PLATFORM_ORG_ID": "01JD8Z3K9Q4M7T2V6X8B5N1C0A",
    "CURSOR_HMAC_KEYS": "k" * 32 + "," + "j" * 32,
}


def test_settings_parse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.cognito_client_ids == ["a", "b"]
    assert len(s.cursor_hmac_keys) == 2
    assert s.issuer.endswith("/ap-southeast-1_X")


@pytest.mark.parametrize(
    "bad", [{"PLATFORM_ORG_ID": "nope"}, {"CURSOR_HMAC_KEYS": "short"}, {"COGNITO_CLIENT_IDS": " "}]
)
def test_settings_refuse_bad_values(monkeypatch: pytest.MonkeyPatch, bad: dict[str, str]) -> None:
    for k, v in {**BASE, **bad}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_refuse_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in BASE:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_redaction() -> None:
    out = redact({"email": "a@b.c", "nested": {"phone": "1"}, "org_id": "x", "Authorization": "t"})
    assert out == {
        "email": "[redacted]",
        "nested": {"phone": "[redacted]"},
        "org_id": "x",
        "Authorization": "[redacted]",
    }


def test_json_log_line() -> None:
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "hello", None, None)
    rec.request_id = "r1"
    rec.email = "secret@x.y"
    doc = json.loads(JsonFormatter("test").format(rec))
    assert doc["msg"] == "hello" and doc["request_id"] == "r1" and doc["email"] == "[redacted]"
    assert "secret@x.y" not in json.dumps(doc)


@mock_aws
def test_field_encryption_roundtrip_and_context_binding() -> None:
    kms = boto3.client("kms", region_name="ap-southeast-1")
    key_id = kms.create_key()["KeyMetadata"]["KeyId"]
    enc = FieldEncryptor(kms, key_id)
    ctx = {"org_id": "o1", "tenant_id": "t1", "field": "identity_document.number"}
    blob = enc.encrypt("900101-01-1234", ctx)
    assert "900101" not in json.dumps(blob)
    assert enc.decrypt(blob, ctx) == "900101-01-1234"
    assert enc.decrypt(enc.rewrap(blob, ctx), ctx) == "900101-01-1234"
    with pytest.raises(Exception):  # noqa: B017, PT011 - wrong context must not decrypt
        enc.decrypt(blob, {**ctx, "org_id": "o2"})
    assert mask("900101011234") == "********1234"
