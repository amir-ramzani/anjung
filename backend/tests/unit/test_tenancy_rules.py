"""Static rules that keep tenancy by construction (LLD 3.3, rule 5)."""

import re
from pathlib import Path

from tests.contract.test_contract import flatten_routes

APP = Path(__file__).parents[2] / "app"
ALLOWED_ORG_ARGS = re.compile(r"^(self\._ctx\.org_id|ctx\.org_id|self\.org_id)$")


def test_no_org_id_parameter_outside_platform_routes(container) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    for (_, path), route in flatten_routes(create_app(container)).items():
        if path.startswith("/v1/platform"):
            continue
        params = {p.name for p in route.dependant.path_params + route.dependant.query_params}
        params |= {p.name for p in route.dependant.header_params}
        assert "org_id" not in params, f"{path} accepts org_id from the caller"
        body = route.body_field.field_info.annotation if route.body_field else None
        assert "org_id" not in getattr(body, "model_fields", {}), f"{path} body carries org_id"


def test_repositories_are_built_from_the_token_org_only() -> None:
    pattern = re.compile(
        r"store\.(?:org|members|properties|units|audit|tenants|leases|documents|billing|privacy)\(([^)]*)\)"
    )
    for f in (APP / "domains").rglob("*.py"):
        platform = "platform" in f.parts
        for arg in pattern.findall(f.read_text()):
            arg = arg.strip()
            # privacy/export and anonymise are internal functions: their org_id comes from the
            # caller's token (service) or from a trusted job envelope written by the service
            internal = (
                (f.name in ("export.py", "anonymise.py") and "privacy" in f.parts)
                or (
                    "billing" in f.parts
                    and f.name
                    in ("invoicing.py", "runs.py", "payments.py", "receipts.py", "jobs.py")
                )
                or (
                    "notifications" in f.parts and f.name == "service.py"
                )  # called by jobs with the envelope's org
                or ("reports" in f.parts and f.name == "service.py")  # the router passes ctx.org_id
                or (
                    "maintenance" in f.parts and f.name == "jobs.py"
                )  # called with the envelope's org
                # shared lifecycle helpers and the retention purge: called with the caller's
                # ctx.org_id (services) or the job envelope's org
                or (f.name == "lifecycle.py" and f.parent.name == "domains")
                or (f.name == "retention.py" and "privacy" in f.parts)
                # the plan: called with the caller's ctx.org_id, or the job envelope's org (reminders)
                or (f.name == "plan.py" and "organisations" in f.parts)
                # staff welcome emails: called with the caller's ctx.org_id, or the organisation the
                # platform admin is creating or resending for (contract v0.15.0)
                or (f.name == "staff_invite.py" and "organisations" in f.parts)
            )
            # the public set-password link: the org is read from the stored link record, never the request
            link = f.name == "invites.py" and "tenants" in f.parts and arg == 'link["org_id"]'
            # the public brand pages: the org is read from the handle registry, never the request
            link = link or (
                f.name in ("brands.py", "branding.py")
                and "organisations" in f.parts
                and arg in ('meta["id"]', 'ref["org_id"]', "org_id")
            )
            ok = (
                link
                or ALLOWED_ORG_ARGS.match(arg)
                or ((platform or internal) and arg in ("org_id", "self._org"))
            )
            assert ok, f"{f.relative_to(APP)}: store repo built from `{arg}`"


def test_platform_repository_is_confined() -> None:
    allowed = {
        "domains/platform",
        "lambdas",
        "bootstrap.py",
        "repositories",
        "container.py",
        "worker",
        "domains/privacy/retention.py",  # deleting closed organisations (platform job)
    }
    for f in APP.rglob("*.py"):
        rel = str(f.relative_to(APP))
        text = f.read_text()
        if ("store.platform(" in text or "PlatformRepository" in text) and not any(
            rel.startswith(a) for a in allowed
        ):
            raise AssertionError(f"{rel} uses cross-org access")


def test_routers_do_not_import_repositories_or_boto() -> None:
    for f in (APP / "domains").rglob("router.py"):
        text = f.read_text()
        assert "app.repositories" not in text and "boto3" not in text, f.name
