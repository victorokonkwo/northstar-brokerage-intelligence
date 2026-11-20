"""Correcting TerraVision's artifacts keeps every other node and edge; a changed graph fails."""

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
        "aws_redshiftserverless_usage_limit.monthly": [
            "aws_cloudwatch_metric_alarm.monthly_compute",
            *workgroups,
        ],
        "aws_cloudwatch_metric_alarm.monthly_compute": [
            "aws_redshiftserverless_usage_limit.monthly",
            *workgroups,
        ],
        "aws_sns_topic.sns": ["aws_cloudwatch_metric_alarm.monthly_compute"],
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


def test_data_graph_keeps_one_workgroup_and_adds_the_copy_alert_and_operator_edges() -> None:
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
        "aws_cloudwatch_metric_alarm.monthly_compute": ["aws_sns_topic.sns"],
        "aws_sns_topic.sns": ["tv_aws_users.operator"],
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


def _batch_graph() -> dict[str, list[str]]:
    """TerraVision 0.56.4's batch-stack export, as drawn from the stack at 4G."""
    policies = [f"aws_iam_role_policy.{r}" for r in ("execution", "scheduler", "states", "task")]
    return {
        "aws_az.availability_zone_ca_central_1": ["aws_subnets.warehouse"],
        "aws_cloudwatch_log_group.cloudwatch": ["aws_fargate.ecs"],
        "aws_cloudwatch_metric_alarm.daily_close_failed": [],
        "aws_ecr_lifecycle_policy.batch": ["aws_ecr_repository.batch"],
        "aws_ecr_repository.batch": ["aws_ecr_lifecycle_policy.batch", "aws_fargate.ecs"],
        "aws_ecs_cluster.ecs": [],
        "aws_fargate.ecs": [
            "aws_cloudwatch_log_group.cloudwatch",
            "aws_ecr_repository.batch",
            "aws_ecs_cluster.ecs",
        ],
        "aws_group.aws_iam_role_policy": list(policies),
        "aws_group.shared_services": [
            "aws_cloudwatch_log_group.cloudwatch",
            "aws_ecr_repository.batch",
            "aws_iam_group.of_services",
        ],
        "aws_iam_role.execution": ["aws_fargate.ecs", "aws_iam_role_policy.execution"],
        "aws_iam_role.scheduler": [
            "aws_iam_role_policy.scheduler",
            "aws_scheduler_schedule.daily_close",
        ],
        "aws_iam_role.states": ["aws_iam_role_policy.states"],
        "aws_iam_role.task": ["aws_fargate.ecs", "aws_iam_role_policy.task"],
        **{policy: [] for policy in policies},
        "aws_scheduler_schedule.daily_close": [],
        "aws_security_group.task_daily_close": ["aws_sfn_state_machine.daily_close"],
        "aws_sfn_state_machine.daily_close": [
            "aws_cloudwatch_metric_alarm.daily_close_failed",
            "aws_fargate.ecs",
            "aws_iam_role.states",
            "aws_scheduler_schedule.daily_close",
            "aws_sns_topic.sns",
        ],
        "aws_sns_topic.sns": [],
        "aws_subnets.warehouse": ["aws_security_group.task_daily_close"],
        "aws_vpc.warehouse": ["tv_blank.empty~1"],
        "tv_blank.empty~1": [],
    }


def test_batch_graph_puts_the_tasks_in_the_vpc_and_points_edges_the_right_way() -> None:
    out = _prune().prune(_batch_graph(), "batch")

    assert out == {
        "aws_cloudwatch_log_group.cloudwatch": [],
        "aws_cloudwatch_metric_alarm.daily_close_failed": ["aws_sns_topic.sns"],
        "aws_ecr_lifecycle_policy.batch": ["aws_ecr_repository.batch"],
        "aws_ecr_repository.batch": ["aws_ecr_lifecycle_policy.batch"],
        "aws_ecs_cluster.ecs": [],
        "aws_fargate.ecs": [
            "aws_cloudwatch_log_group.cloudwatch",
            "aws_ecr_repository.batch",
            "aws_ecs_cluster.ecs",
        ],
        "aws_iam_role.execution": ["aws_fargate.ecs"],
        "aws_iam_role.scheduler": ["aws_scheduler_schedule.daily_close"],
        "aws_iam_role.states": ["aws_sfn_state_machine.daily_close"],
        "aws_iam_role.task": ["aws_fargate.ecs"],
        "aws_scheduler_schedule.daily_close": ["aws_sfn_state_machine.daily_close"],
        "aws_security_group.task_daily_close": ["aws_fargate.ecs"],
        "aws_sfn_state_machine.daily_close": ["aws_fargate.ecs", "aws_sns_topic.sns"],
        "aws_sns_topic.sns": [],
        "aws_vpc.warehouse": ["aws_security_group.task_daily_close"],
        "tv_aws_users.operator": ["aws_sfn_state_machine.daily_close"],
    }


def test_rewiring_a_node_the_graph_lacks_fails() -> None:
    p = _prune()
    graph = _batch_graph()
    del graph["aws_scheduler_schedule.daily_close"]
    graph["aws_iam_role.scheduler"] = ["aws_iam_role_policy.scheduler"]
    graph["aws_sfn_state_machine.daily_close"].remove("aws_scheduler_schedule.daily_close")

    with pytest.raises(p.UnexpectedGraphError, match=r"aws_scheduler_schedule\.daily_close"):
        p.prune(graph, "batch")
