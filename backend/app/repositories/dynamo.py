"""Thin DynamoDB wrapper: typed (de)serialisation, conditional writes, transactions."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

_ser, _de = TypeSerializer(), TypeDeserializer()


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def _unclean(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _unclean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unclean(x) for x in v]
    return v


def to_ddb(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(_clean(v)) for k, v in item.items()}


def from_ddb(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _unclean(_de.deserialize(v)) for k, v in item.items()}


class ConditionFailed(Exception):
    pass


class TxCancelled(Exception):
    """Transaction cancelled. `reasons[i]` is the failure code of operation i (None if fine)."""

    def __init__(self, reasons: list[str | None]) -> None:
        super().__init__(f"transaction cancelled: {reasons}")
        self.reasons = reasons

    def failed(self, index: int) -> bool:
        return index < len(self.reasons) and self.reasons[index] == "ConditionalCheckFailed"

    @property
    def conflicted(self) -> bool:
        """Another transaction was touching the same item at that moment: safe to retry."""
        return any(r in ("TransactionConflict", "ThrottlingError") for r in self.reasons)


@dataclass
class _Cond:
    condition: str | None = None
    names: dict[str, str] | None = None
    values: dict[str, Any] | None = None

    def _apply(self, req: dict[str, Any]) -> None:
        if self.condition:
            req["ConditionExpression"] = self.condition
        if self.names:
            req["ExpressionAttributeNames"] = dict(self.names)
        if self.values:
            req["ExpressionAttributeValues"] = to_ddb(self.values)


@dataclass
class Put(_Cond):
    item: dict[str, Any] = None  # type: ignore[assignment]


@dataclass
class Update(_Cond):
    key: tuple[str, str] = ("", "")
    expression: str = ""


@dataclass
class Delete(_Cond):
    key: tuple[str, str] = ("", "")


@dataclass
class Check(_Cond):
    key: tuple[str, str] = ("", "")


NOT_EXISTS = "attribute_not_exists(PK)"


def _key(k: tuple[str, str]) -> dict[str, Any]:
    return to_ddb({"PK": k[0], "SK": k[1]})


def _tx_item(op: Put | Update | Delete | Check, table: str) -> dict[str, Any]:
    if isinstance(op, Put):
        req: dict[str, Any] = {"TableName": table, "Item": to_ddb(op.item)}
        op._apply(req)
        return {"Put": req}
    if isinstance(op, Update):
        req = {"TableName": table, "Key": _key(op.key), "UpdateExpression": op.expression}
        op._apply(req)
        return {"Update": req}
    if isinstance(op, Delete):
        req = {"TableName": table, "Key": _key(op.key)}
        op._apply(req)
        return {"Delete": req}
    req = {"TableName": table, "Key": _key(op.key)}
    op._apply(req)
    return {"ConditionCheck": req}


class DynamoTable:
    def __init__(self, client: Any, name: str) -> None:
        self._c, self.name = client, name

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        r = self._c.get_item(TableName=self.name, Key=_key((pk, sk)), ConsistentRead=True)
        return from_ddb(r["Item"]) if "Item" in r else None

    def put(self, item: dict[str, Any], condition: str | None = None) -> None:
        req: dict[str, Any] = {"TableName": self.name, "Item": to_ddb(item)}
        if condition:
            req["ConditionExpression"] = condition
        try:
            self._c.put_item(**req)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConditionFailed() from None
            raise

    def query(
        self,
        *,
        pk: str,
        sk_prefix: str | None = None,
        index: str | None = None,
        forward: bool = True,
        limit: int | None = None,
        start_key: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        pk_attr, sk_attr = (f"{index}PK", f"{index}SK") if index else ("PK", "SK")
        names = {"#pk": pk_attr}
        values: dict[str, Any] = {":pk": pk}
        cond = "#pk = :pk"
        if sk_prefix is not None:
            names["#sk"] = sk_attr
            values[":sk"] = sk_prefix
            cond += " AND begins_with(#sk, :sk)"
        req: dict[str, Any] = {
            "TableName": self.name,
            "KeyConditionExpression": cond,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": to_ddb(values),
            "ScanIndexForward": forward,
        }
        if index:
            req["IndexName"] = index
        else:
            req["ConsistentRead"] = True
        if limit:
            req["Limit"] = limit
        if start_key:
            req["ExclusiveStartKey"] = to_ddb(start_key)
        r = self._c.query(**req)
        lek = from_ddb(r["LastEvaluatedKey"]) if "LastEvaluatedKey" in r else None
        return [from_ddb(i) for i in r.get("Items", [])], lek

    def scan_org(self, org_id: str) -> list[dict[str, Any]]:
        """Every item carrying this org_id. A full table scan: only for deleting a closed organisation."""
        out: list[dict[str, Any]] = []
        req: dict[str, Any] = {
            "TableName": self.name,
            "FilterExpression": "org_id = :o",
            "ExpressionAttributeValues": to_ddb({":o": org_id}),
            "ConsistentRead": True,
        }
        while True:
            r = self._c.scan(**req)
            out += [from_ddb(i) for i in r.get("Items", [])]
            if "LastEvaluatedKey" not in r:
                return out
            req["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def batch_get(self, keys: list[tuple[str, str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(keys), 100):
            pending: dict[str, Any] = {
                self.name: {"Keys": [_key(k) for k in keys[i : i + 100]], "ConsistentRead": True}
            }
            while pending:
                r = self._c.batch_get_item(RequestItems=pending)
                out += [from_ddb(x) for x in r["Responses"].get(self.name, [])]
                pending = r.get("UnprocessedKeys") or {}
        return out

    def transact(self, ops: list[Put | Update | Delete | Check]) -> None:
        if not 0 < len(ops) <= 100:
            raise ValueError("a transaction holds 1 to 100 operations")
        from app.core import tracing

        cancelled: TxCancelled | None = None
        with tracing.span(
            "dynamodb.transact",
            attributes={
                "db.system": "dynamodb",
                "db.operation": "TransactWriteItems",
                "db.operation_count": len(ops),
            },
        ) as sp:
            try:
                self._c.transact_write_items(TransactItems=[_tx_item(o, self.name) for o in ops])
            except ClientError as e:
                if e.response["Error"]["Code"] != "TransactionCanceledException":
                    raise
                reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
                sp.set_attribute(
                    "db.outcome", "cancelled"
                )  # expected for a duplicate: not an error
                cancelled = TxCancelled([None if r == "None" else r for r in reasons])
        if cancelled is not None:
            raise cancelled

    def ping(self) -> None:
        self._c.describe_table(TableName=self.name)
