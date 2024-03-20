"""Local developer commands: `python -m app.cli create-table`, `bootstrap-platform`."""

import argparse
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

from app.bootstrap import bootstrap_platform
from app.container import Container
from app.core.config import Settings
from app.integrations.identity import UserExists
from app.repositories.dynamo import TxCancelled
from app.repositories.schema import table_definition


def _create_bucket(settings: Settings) -> None:
    s3 = boto3.client("s3", region_name=settings.aws_region, endpoint_url=settings.s3_endpoint_url)
    for attempt in range(30):  # the local S3 container may still be installing
        try:
            s3.create_bucket(
                Bucket=settings.docs_bucket,
                CreateBucketConfiguration={"LocationConstraint": settings.aws_region},
            )
            print(f"created bucket {settings.docs_bucket}")
            return
        except EndpointConnectionError:
            if attempt == 29:
                raise
            time.sleep(2)
        except ClientError as e:
            if e.response["Error"]["Code"] not in (
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            ):
                raise
            print(f"bucket {settings.docs_bucket} already exists")
            return


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-table", help="create the DynamoDB table (DynamoDB Local only)")
    rj = sub.add_parser(
        "run-job", help="run one scheduled job now, for example integrity_check or overdue_sweep"
    )
    rj.add_argument("job")
    rj.add_argument(
        "--org", help="organisation id (the *_all fan-outs and reconcile_payments ignore it)"
    )
    bp = sub.add_parser("bootstrap-platform", help="create the platform org and first admin")
    bp.add_argument("--email", required=True)
    bp.add_argument("--name")
    sd = sub.add_parser("seed-demo", help="local only: create a demo organisation, users and data")
    sd.add_argument("--platform-admin", default="root@example.com")
    sd.add_argument(
        "--email-domain",
        default="example.com",
        help="domain for the demo users, if example.com is taken",
    )
    args = parser.parse_args()

    settings = Settings()
    if args.cmd == "create-table":
        if settings.env not in ("local", "test"):
            raise SystemExit(
                "create-table is for local DynamoDB only; tables elsewhere come from CDK"
            )
        client = boto3.client(
            "dynamodb", region_name=settings.aws_region, endpoint_url=settings.dynamodb_endpoint_url
        )
        for attempt in range(30):  # DynamoDB Local may still be starting (docker compose)
            try:
                client.create_table(**table_definition(settings.table_name))
                print(f"created {settings.table_name}")
                break
            except EndpointConnectionError:
                if attempt == 29:
                    raise
                time.sleep(1)
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceInUseException":
                    raise
                print(f"{settings.table_name} already exists")
                break
        if settings.docs_bucket:
            _create_bucket(settings)
    elif args.cmd == "seed-demo":
        import warnings

        # seed-demo drives the API through Starlette's TestClient, whose import warns about httpx: noise here
        warnings.filterwarnings("ignore", message=".*starlette.testclient.*")
        from app.seed import seed

        print("\n".join(seed(settings, args.platform_admin, args.email_domain)))
    elif args.cmd == "run-job":
        from app.worker.envelope import make_envelope
        from app.worker.handlers.housekeeping import build_handlers

        c = Container.build(settings)
        handler = build_handlers(c).get(args.job)
        if handler is None:
            raise SystemExit(
                f"unknown job {args.job}. Known: {', '.join(sorted(build_handlers(c)))}"
            )
        handler(make_envelope(args.job, args.org or settings.platform_org_id, {}, c.clock.now()))
        print(f"ran {args.job}")
    else:
        c = Container.build(settings)
        try:
            sub_id = bootstrap_platform(
                c.store.table,
                c.identity,
                c.clock,
                platform_org_id=settings.platform_org_id,
                admin_email=args.email,
                admin_name=args.name,
            )
            print(f"platform admin created, sub={sub_id}")
        except (UserExists, TxCancelled):
            print("platform organisation already bootstrapped, nothing to do")


if __name__ == "__main__":
    main()
