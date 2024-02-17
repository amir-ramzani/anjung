"""Table definition (LLD 4.1). Used by tests and the local CLI so GSIs match production."""

from typing import Any


def _gsi(n: int, projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "IndexName": f"GSI{n}",
        "KeySchema": [
            {"AttributeName": f"GSI{n}PK", "KeyType": "HASH"},
            {"AttributeName": f"GSI{n}SK", "KeyType": "RANGE"},
        ],
        "Projection": projection,
    }


def table_definition(name: str) -> dict[str, Any]:
    all_attrs = {"ProjectionType": "ALL"}
    keys_only = {"ProjectionType": "KEYS_ONLY"}
    # GSI2 (login resolution) also projects what the Pre Token Generation Lambda needs.
    gsi2 = {
        "ProjectionType": "INCLUDE",
        "NonKeyAttributes": [
            "org_id",
            "roles",
            "entity_type",
            "status",
            "tenant_id",
            "email",
            "name",
        ],
    }
    attrs = ["PK", "SK"] + [f"GSI{n}{k}" for n in range(1, 6) for k in ("PK", "SK")]
    return {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            _gsi(1, all_attrs),
            _gsi(2, gsi2),
            _gsi(3, all_attrs),
            _gsi(4, all_attrs),
            _gsi(5, keys_only),
        ],
    }
