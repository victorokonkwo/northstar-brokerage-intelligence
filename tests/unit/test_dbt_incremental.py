"""Every incremental dbt model rebuilds on exactly the inputs it reads (roadmap 4F-b).

An incremental model rebuilds only what records from its ``meta.rebuild.inputs``
affect (``warehouse/dbt/macros/incremental.sql``). A staging model it reads but
doesn't list would never trigger a rebuild, so its numbers would silently go
stale. This test parses the dbt project (no database) and checks each
incremental model against its real dependency graph.
"""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

DBT_PROJECT = Path(__file__).parents[2] / "warehouse" / "dbt"
DBT = Path(sys.executable).parent / "dbt"
ARRIVALS = "model.northstar.int_arrivals"
ARRIVAL_KEYS = frozenset({"record_id", "account_id", "order_id", "settlement_id", "instrument_id"})


def _manifest(target: Path) -> dict[str, Any]:
    env = {
        **os.environ,
        # dbt parse renders the profile but never connects.
        "NBI_PG_USER": os.environ.get("NBI_PG_USER", "parse-only"),
        "NBI_PG_PASSWORD": os.environ.get("NBI_PG_PASSWORD", "parse-only"),
    }
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [str(DBT), "parse", "--target-path", str(target), "--log-path", str(target / "logs")],
        cwd=DBT_PROJECT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:]
    manifest: dict[str, Any] = json.loads((target / "manifest.json").read_text())
    return manifest


@pytest.fixture(scope="module")
def manifest(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _manifest(tmp_path_factory.mktemp("dbt-target"))


def _models(manifest: dict[str, Any]) -> Iterator[dict[str, Any]]:
    return (n for n in manifest["nodes"].values() if n["resource_type"] == "model")


def _staging_inputs(manifest: dict[str, Any], unique_id: str) -> set[str]:
    """The staging models a model reads, directly or through other models (not int_arrivals)."""
    found: set[str] = set()
    stack = list(manifest["nodes"][unique_id]["depends_on"]["nodes"])
    seen: set[str] = set()
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id == ARRIVALS or not node_id.startswith("model."):
            continue
        seen.add(node_id)
        node = manifest["nodes"][node_id]
        if node["name"].startswith("stg_"):
            found.add(node["name"])
        else:
            stack.extend(node["depends_on"]["nodes"])
    return found


def test_every_incremental_model_is_configured_for_exact_rebuilds(
    manifest: dict[str, Any],
) -> None:
    incremental = [m for m in _models(manifest) if m["config"]["materialized"] == "incremental"]
    assert len(incremental) >= 17
    for model in incremental:
        config = model["config"]
        assert config["incremental_strategy"] == "rebuild", model["name"]
        assert config["on_schema_change"] == "fail", model["name"]
        assert any("record_build()" in hook["sql"] for hook in config["post-hook"]), model["name"]
        rebuild = config["meta"].get("rebuild")
        assert rebuild is not None, model["name"]
        if model["unique_id"] == ARRIVALS:
            assert rebuild == {"new": True}
            continue
        assert ARRIVALS in model["depends_on"]["nodes"], model["name"]
        assert ("date" in rebuild) != ("key" in rebuild), model["name"]
        if "key" in rebuild:
            assert rebuild["arrival"] in ARRIVAL_KEYS, model["name"]


def test_every_incremental_model_rebuilds_on_exactly_what_it_reads(
    manifest: dict[str, Any],
) -> None:
    recorded = _staging_inputs(manifest, ARRIVALS)
    for model in _models(manifest):
        rebuild = model["config"]["meta"].get("rebuild")
        if rebuild is None or rebuild.get("new"):
            continue
        reads = _staging_inputs(manifest, model["unique_id"])
        assert set(rebuild["inputs"]) == reads, model["name"]
        assert reads <= recorded, (model["name"], reads - recorded)
