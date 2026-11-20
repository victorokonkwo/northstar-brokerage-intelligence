"""Pruning TerraVision's artifacts keeps every other node and edge, and fails on a changed graph."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]


def _prune() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "terravision_prune", REPO / "scripts" / "terravision_prune.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data_graph() -> dict[str, list[str]]:
    """The shape of TerraVision 0.56.4's data-stack export, cut down to the relevant nodes."""
    workgroups = [f"aws_redshiftserverless_workgroup.warehouse~{i}" for i in (1, 2, 3, 4)]
    return {
        "aws_group.shared_services": ["aws_iam_group.of_services"],
        "aws_iam_role.redshift_copy": ["aws_redshiftserverless_namespace.warehouse"],
        "aws_redshiftserverless_namespace.warehouse": list(workgroups),
        "aws_redshiftserverless_usage_limit.daily": list(workgroups),
        "aws_redshiftserverless_usage_limit.monthly": list(workgroups),
        **{
            wg: [
                "aws_redshiftserverless_namespace.warehouse",
                "aws_redshiftserverless_usage_limit.daily",
            ]
            for wg in workgroups
        },
        **{
            f"aws_security_group.warehouse_warehouse~{i}": [workgroups[i - 1]] for i in (1, 2, 3, 4)
        },
        "aws_s3_bucket.bronze": ["aws_s3_bucket_ownership_controls.bronze"],
        "aws_s3_bucket_ownership_controls.bronze": ["aws_s3_bucket.bronze"],
        "tv_aws_internet.internet": [],
    }


def test_data_graph_keeps_one_workgroup_and_adds_the_copy_and_operator_edges() -> None:
    out = _prune().prune(_data_graph(), "data")

    assert out == {
        "aws_iam_role.redshift_copy": [
            "aws_redshiftserverless_namespace.warehouse",
            "aws_s3_bucket.bronze",
        ],
        "aws_redshiftserverless_namespace.warehouse": [
            "aws_redshiftserverless_workgroup.warehouse~1"
        ],
        "aws_redshiftserverless_workgroup.warehouse~1": [
            "aws_redshiftserverless_namespace.warehouse"
        ],
        "aws_security_group.warehouse_warehouse~1": [
            "aws_redshiftserverless_workgroup.warehouse~1"
        ],
        "aws_s3_bucket.bronze": [],
        "tv_aws_internet.internet": [],
        "tv_aws_users.operator": ["aws_s3_bucket.bronze", "tv_aws_internet.internet"],
    }


def test_a_missing_artifact_fails_instead_of_passing_silently() -> None:
    p = _prune()
    graph = _data_graph()
    del graph["aws_security_group.warehouse_warehouse~4"]

    with pytest.raises(p.UnexpectedGraphError, match="warehouse_warehouse~4"):
        p.prune(graph, "data")


def test_an_added_edge_to_a_missing_resource_fails() -> None:
    p = _prune()
    graph = _data_graph()
    graph["aws_s3_bucket_ownership_controls.bronze"] = []
    del graph["aws_s3_bucket.bronze"]

    with pytest.raises(p.UnexpectedGraphError, match=r"aws_s3_bucket\.bronze"):
        p.prune(graph, "data")


def test_bootstrap_graph_drops_only_its_artifacts() -> None:
    graph = {
        "aws_group.shared_services": ["aws_iam_group.of_services"],
        "aws_budgets_budget.monthly": [],
        "aws_s3_bucket.state": ["aws_s3_bucket_ownership_controls.state"],
        "aws_s3_bucket_ownership_controls.state": ["aws_s3_bucket.state"],
    }

    assert _prune().prune(graph, "bootstrap") == {
        "aws_budgets_budget.monthly": [],
        "aws_s3_bucket.state": [],
    }
