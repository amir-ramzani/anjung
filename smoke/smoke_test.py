"""Post-deploy smoke test (LLD 16.2). Skeleton: checks 1 and 2. Sign-in checks arrive with P1-12.

    uv run --with httpx smoke/smoke_test.py --base-url https://api.dev.anjung.my
"""

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable

import httpx


def check_health(c: httpx.Client) -> None:
    assert c.get("/healthz").status_code == 200, "healthz"
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ok", f"readyz {r.status_code}"


def check_unauthenticated(c: httpx.Client) -> None:
    r = c.get("/v1/me")
    assert r.status_code == 401, f"expected 401, got {r.status_code}"
    assert r.json()["code"] == "unauthorized"


CHECKS: list[tuple[str, Callable[[httpx.Client], None]]] = [
    ("1 health and readiness", check_health),
    ("2 unauthenticated request is rejected", check_unauthenticated),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--junit", default="smoke/results/junit.xml")
    args = ap.parse_args()
    suite = ET.Element("testsuite", name="smoke")
    failed = 0
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        for name, fn in CHECKS:
            start, err = time.perf_counter(), None
            try:
                fn(client)
            except Exception as e:  # noqa: BLE001
                err = str(e) or type(e).__name__
            took = time.perf_counter() - start
            print(f"{'PASS' if err is None else 'FAIL'}  {name}  {took * 1000:.0f} ms" + (f"  {err}" if err else ""))
            case = ET.SubElement(suite, "testcase", name=name, time=f"{took:.3f}")
            if err:
                failed += 1
                ET.SubElement(case, "failure", message=err)
    suite.set("tests", str(len(CHECKS)))
    suite.set("failures", str(failed))
    import pathlib

    pathlib.Path(args.junit).parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(args.junit)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
