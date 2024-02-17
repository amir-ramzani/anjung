import pytest
from app.core.errors import BadRequest
from app.core.pagination import CursorCodec

KEY = {"PK": "ORG#A", "SK": "PROP#1"}


def test_roundtrip() -> None:
    c = CursorCodec([b"a" * 32])
    token = c.encode(key=KEY, index=None, pk="ORG#A")
    assert c.decode(token, pk="ORG#A", index=None) == KEY


def test_rejects_other_partition_and_index() -> None:
    c = CursorCodec([b"a" * 32])
    token = c.encode(key=KEY, index=None, pk="ORG#A")
    with pytest.raises(BadRequest):
        c.decode(token, pk="ORG#B", index=None)
    with pytest.raises(BadRequest):
        c.decode(token, pk="ORG#A", index="GSI1")


def test_rejects_tampering_and_garbage() -> None:
    c = CursorCodec([b"a" * 32])
    body, sig = c.encode(key=KEY, index=None, pk="ORG#A").split(".")
    forged = CursorCodec([b"b" * 32]).encode(key=KEY, index=None, pk="ORG#A")
    for bad in (f"{body}x.{sig}", forged, "garbage", "", "a.b"):
        with pytest.raises(BadRequest):
            c.decode(bad, pk="ORG#A", index=None)


def test_key_rotation_overlap() -> None:
    old, new = b"o" * 32, b"n" * 32
    token = CursorCodec([old]).encode(key=KEY, index=None, pk="ORG#A")
    assert CursorCodec([new, old]).decode(token, pk="ORG#A", index=None) == KEY
    with pytest.raises(BadRequest):
        CursorCodec([new]).decode(token, pk="ORG#A", index=None)
