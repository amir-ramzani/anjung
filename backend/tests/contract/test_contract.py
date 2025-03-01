"""The contract (contracts/openapi.json) is the source of truth. These tests keep the code honest."""

import json
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from fastapi.routing import APIRoute

CONTRACT = json.loads((Path(__file__).parents[3] / "contracts" / "openapi.json").read_text())
RAW_BODY_ROUTES = {("POST", "/v1/webhooks/payments/{provider}")}
METHODS = {"get", "post", "put", "patch", "delete"}


def contract_ops() -> dict[tuple[str, str], dict[str, Any]]:
    out = {}
    for path, item in CONTRACT["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                out[(method.upper(), path)] = op
    return out


def _required_roles(route: APIRoute) -> frozenset[str] | None:
    def walk(dep: Any) -> frozenset[str] | None:
        for d in dep.dependencies:
            roles = getattr(d.call, "allowed_roles", None)
            if roles is not None:
                return frozenset(r.value for r in roles)
            found = walk(d)
            if found is not None:
                return found
        return None

    return walk(route.dependant)


def flatten_routes(app: Any) -> dict[tuple[str, str], APIRoute]:
    """App routes keyed by (METHOD, full path). FastAPI wraps included routers lazily."""
    out: dict[tuple[str, str], APIRoute] = {}
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods:
                out[(m, r.path)] = r
        elif hasattr(r, "original_router"):
            prefix = r.include_context.prefix
            for inner in r.original_router.routes:
                for m in inner.methods:
                    out[(m, prefix + inner.path)] = inner
    return out


@pytest.fixture
def routes(container) -> dict[tuple[str, str], APIRoute]:  # type: ignore[no-untyped-def]
    return flatten_routes(create_app(container))


def test_every_route_is_in_the_contract(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    extra = [k for k in routes if k not in ops]
    assert not extra, f"routes missing from contract: {extra}"


def test_operation_ids_match(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    for key, route in routes.items():
        assert route.operation_id == ops[key]["operationId"], key


def test_required_roles_match_x_required_roles(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    for key, route in routes.items():
        expected = ops[key].get("x-required-roles")
        actual = _required_roles(route)
        if expected is None:
            assert actual is None, f"{key} is public in the contract but has role checks"
        else:
            assert actual == frozenset(expected), f"{key}: code {actual}, contract {expected}"


def test_status_codes_match(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    for key, route in routes.items():
        success = next(c for c in ops[key]["responses"] if c.startswith("2"))
        if route.status_code is not None:
            assert str(route.status_code) == success, key


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return CONTRACT["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def test_request_schemas_have_the_contract_fields(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    for key, route in routes.items():
        body = ops[key].get("requestBody")
        if not body:
            continue
        spec = _resolve(body["content"]["application/json"]["schema"])
        if route.body_field is None and key in RAW_BODY_ROUTES:
            continue  # read as raw bytes on purpose: the signature covers the exact bytes
        model = route.body_field.field_info.annotation if route.body_field else None
        assert model is not None, key
        if not hasattr(model, "model_fields"):  # an optional body: `Model | None`
            assert body.get("required") is False, f"{key}: optional body must be required: false"
            model = next(a for a in model.__args__ if hasattr(a, "model_fields"))
        fields = {(f.alias or n) for n, f in model.model_fields.items()}
        assert fields == set(spec["properties"]), f"{key}: {fields} vs {set(spec['properties'])}"
        required = {n for n, f in model.model_fields.items() if f.is_required()}
        assert required == set(spec.get("required", [])), f"{key} required fields differ"


def test_response_models_cover_contract_required_fields(routes: dict) -> None:  # type: ignore[type-arg]
    ops = contract_ops()
    for key, route in routes.items():
        ok = next(c for c in ops[key]["responses"] if c.startswith("2"))
        content = ops[key]["responses"][ok].get("content")
        if not content or not hasattr(route.response_model, "model_fields"):
            continue
        spec = _resolve(content["application/json"]["schema"])
        fields = {f.alias or n for n, f in route.response_model.model_fields.items()}
        assert set(spec.get("required", [])) <= fields, f"{key}: missing required response fields"
        assert fields <= set(spec["properties"]), (
            f"{key}: extra response fields {fields - set(spec['properties'])}"
        )


def test_implementation_progress(routes: dict) -> None:  # type: ignore[type-arg]
    """Phases 2 to 5 add the rest. This pins what phase 1 must have."""
    assert (
        len(routes) >= 23  # health 2, me, org 2, users 4, platform 4, properties 5, units 4, audit
    )  # health (2) + me, org (2), users (4), platform (4), properties/units (8), audit
    assert {k for k in contract_ops() if k[1].startswith("/v1/platform")} <= set(routes)
