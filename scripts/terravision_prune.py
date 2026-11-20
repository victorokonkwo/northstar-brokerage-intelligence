"""Correct TerraVision's drawing artifacts in a ``terravision graphdata`` export.

TerraVision 0.56.4 builds its graph from ``terraform plan``. For these stacks it:

- draws the data stack's one Redshift workgroup, and its security group, four times
  (``~1`` to ``~4``), with only ``~1`` placed in a subnet;
- adds a "shared services" IAM group that no stack declares;
- draws resources it has no icon for (usage limits, ownership controls) as blank nodes,
  and the batch stack's role policies as separate boxes;
- places the batch stack's resources wrongly, because the VPC, subnets, and alerts topic
  it uses belong to the data stack and are only looked up: the VPC is drawn empty, the
  task security group wraps the state machine instead of the Fargate tasks, and some
  edges point the wrong way.

Its ``remove`` annotations don't reach these nodes, so they are corrected here, in three
steps: drop nodes (``DROP``), replace a node's edges (``REWIRE``), and add edges
(``ADD``). ``ADD`` also draws what a plan can't show: the COPY role reading the bronze
bucket, and the operator, who is outside AWS. Every other node and edge is kept as
TerraVision derived it.

If a node named here is missing, the script fails rather than guessing, because that
means TerraVision's output has changed and the lists below need review.

Usage: ``python scripts/terravision_prune.py <bootstrap|data|batch> <in.tvg.json> <out.tvg.json>``.
Run by ``scripts/draw_architecture.sh`` (``make diagrams``).
"""

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

SYNTHETIC_GROUP = ("aws_group.shared_services", "aws_iam_group.of_services")

DROP: Mapping[str, tuple[str, ...]] = {
    "bootstrap": (*SYNTHETIC_GROUP, "aws_s3_bucket_ownership_controls.state"),
    "data": (
        *SYNTHETIC_GROUP,
        *(f"aws_redshiftserverless_workgroup.warehouse~{i}" for i in (2, 3, 4)),
        *(f"aws_security_group.warehouse_warehouse~{i}" for i in (2, 3, 4)),
        "aws_redshiftserverless_usage_limit.daily",
        "aws_redshiftserverless_usage_limit.monthly",
        "aws_s3_bucket_ownership_controls.bronze",
    ),
    "batch": (
        *SYNTHETIC_GROUP,
        "aws_group.aws_iam_role_policy",
        *(f"aws_iam_role_policy.{role}" for role in ("execution", "scheduler", "states", "task")),
        "tv_blank.empty~1",
        # A lookup of the data stack's three subnets, drawn as one zone and a generic
        # node that nothing can be placed in. The VPC holds the tasks instead.
        "aws_az.availability_zone_ca_central_1",
        "aws_subnets.warehouse",
    ),
}

# A node's edges replaced outright. In the batch stack, containment is
# VPC -> security group -> tasks, as ADR 010 deploys them.
REWIRE: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "bootstrap": {},
    "data": {
        "aws_cloudwatch_metric_alarm.monthly_compute": ("aws_sns_topic.sns",),
        "aws_sns_topic.sns": (),
    },
    "batch": {
        "aws_vpc.warehouse": ("aws_security_group.task_daily_close",),
        "aws_security_group.task_daily_close": ("aws_fargate.ecs",),
        "aws_scheduler_schedule.daily_close": ("aws_sfn_state_machine.daily_close",),
        "aws_sfn_state_machine.daily_close": ("aws_fargate.ecs", "aws_sns_topic.sns"),
        "aws_cloudwatch_metric_alarm.daily_close_failed": ("aws_sns_topic.sns",),
        "aws_iam_role.scheduler": ("aws_scheduler_schedule.daily_close",),
        "aws_iam_role.states": ("aws_sfn_state_machine.daily_close",),
        "aws_iam_role.execution": ("aws_fargate.ecs",),
        "aws_iam_role.task": ("aws_fargate.ecs",),
        "aws_ecr_repository.batch": ("aws_ecr_lifecycle_policy.batch",),
        "aws_cloudwatch_log_group.cloudwatch": (),
    },
}

# Nodes named tv_aws_users.* are external actors that TerraVision draws as people.
ADD: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "bootstrap": {},
    "data": {
        "aws_iam_role.redshift_copy": ("aws_s3_bucket.bronze",),
        "tv_aws_users.operator": ("aws_s3_bucket.bronze", "tv_aws_internet.internet"),
        "aws_sns_topic.sns": ("tv_aws_users.operator",),
    },
    "batch": {
        "tv_aws_users.operator": ("aws_sfn_state_machine.daily_close",),
    },
}


class UnexpectedGraphError(ValueError):
    """The graph doesn't have the shape these lists were written for."""

    def __init__(self, stack: str, problem: str, nodes: Sequence[str]) -> None:
        """Name the stack, what is wrong, and the nodes concerned."""
        super().__init__(f"{problem} in the {stack} graph: {list(nodes)}")


def _nodes(graph: Mapping[str, Sequence[str]]) -> set[str]:
    return set(graph) | {target for targets in graph.values() for target in targets}


def _check_endpoints(
    stack: str, edges: Mapping[str, Sequence[str]], kept: set[str], *, sources_in_graph: bool
) -> None:
    for source, targets in edges.items():
        endpoints = (source, *targets) if sources_in_graph else targets
        unknown = sorted(
            node for node in endpoints if node not in kept and not node.startswith("tv_aws_users.")
        )
        if unknown:
            raise UnexpectedGraphError(stack, "edge endpoints are absent", unknown)


def prune(graph: Mapping[str, Sequence[str]], stack: str) -> dict[str, list[str]]:
    """The graph without ``DROP[stack]``, with ``REWIRE[stack]`` and ``ADD[stack]`` applied.

    Raises:
        KeyError: ``stack`` is not ``bootstrap``, ``data``, or ``batch``.
        UnexpectedGraphError: A node to drop or rewire is absent, or an edge's endpoint
            is neither in the graph nor an external actor.
    """
    drop, rewire, add = set(DROP[stack]), REWIRE[stack], ADD[stack]
    absent = sorted(drop - _nodes(graph))
    if absent:
        raise UnexpectedGraphError(stack, "nodes to drop are absent", absent)
    out = {
        node: [target for target in targets if target not in drop]
        for node, targets in graph.items()
        if node not in drop
    }
    kept = _nodes(out)
    _check_endpoints(stack, rewire, set(out), sources_in_graph=True)
    _check_endpoints(stack, rewire, kept, sources_in_graph=False)
    for source, targets in rewire.items():
        out[source] = list(targets)
    _check_endpoints(stack, add, kept, sources_in_graph=True)
    for source, targets in add.items():
        edges = out.setdefault(source, [])
        edges.extend(target for target in targets if target not in edges)
    return out


def main(argv: Sequence[str]) -> None:
    """Read the export, correct it, and write the result as JSON."""
    stack, source, destination = argv
    graph = json.loads(Path(source).read_text(encoding="utf-8"))
    pruned = prune(graph, stack)
    Path(destination).write_text(json.dumps(pruned, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{stack}: {len(graph)} -> {len(pruned)} nodes\n")


if __name__ == "__main__":
    main(sys.argv[1:])
