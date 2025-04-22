"""The DynamoDB table comes from the backend's own definition (backend/app/repositories/schema.py), so the
tests, the local table and this stack can never drift apart (LLD 16.1)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "app" / "repositories" / "schema.py"


def load() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("anjung_backend_schema", SCHEMA)
    assert spec and spec.loader, f"cannot load {SCHEMA}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definition: dict[str, Any] = module.table_definition("anjung-main")
    return definition
