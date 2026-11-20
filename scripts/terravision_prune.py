"""Remove TerraVision's drawing artifacts from a ``terravision graphdata`` export.

TerraVision 0.56.4 builds its graph from ``terraform plan``, but for these stacks it:

- draws the data stack's one Redshift workgroup, and its security group, four times
  (``~1`` to ``~4``), with only ``~1`` placed in a subnet;
- adds a "shared services" IAM group that neither stack declares;
- draws resources it has no icon for (usage limits, ownership controls) as blank nodes.

Its ``remove`` annotations don't reach these nodes, so they are removed here. The script
also adds the edges a plan can't show: the COPY role reading the bronze bucket, and the
operator, who is outside AWS. Every other node and edge is kept as TerraVision derived it.

If an expected artifact is missing, the script fails rather than guessing, because that
means TerraVision's output has changed and the lists below need review.

Usage: ``python scripts/terravision_prune.py <bootstrap|data> <in.tvg.json> <out.tvg.json>``.
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
}

# Nodes named tv_aws_users.* are external actors that TerraVision draws as people.
ADD: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "bootstrap": {},
    "data": {
        "aws_iam_role.redshift_copy": ("aws_s3_bucket.bronze",),
        "tv_aws_users.operator": ("aws_s3_bucket.bronze", "tv_aws_internet.internet"),
    },
}


class UnexpectedGraphError(ValueError):
    """The graph doesn't have the shape these lists were written for."""

    def __init__(self, stack: str, problem: str, nodes: Sequence[str]) -> None:
        """Name the stack, what is wrong, and the nodes concerned."""
        super().__init__(f"{problem} in the {stack} graph: {list(nodes)}")


def _nodes(graph: Mapping[str, Sequence[str]]) -> set[str]:
    return set(graph) | {target for targets in graph.values() for target in targets}


def prune(graph: Mapping[str, Sequence[str]], stack: str) -> dict[str, list[str]]:
    """The graph without ``DROP[stack]``, with ``ADD[stack]`` edges added.

    Raises:
        KeyError: ``stack`` is not ``bootstrap`` or ``data``.
        UnexpectedGraphError: A node to drop is absent, or an added edge's endpoint is
            neither in the graph nor an external actor.
    """
    drop, add = set(DROP[stack]), ADD[stack]
    absent = sorted(drop - _nodes(graph))
    if absent:
        raise UnexpectedGraphError(stack, "nodes to drop are absent", absent)
    out = {
        node: [target for target in targets if target not in drop]
        for node, targets in graph.items()
        if node not in drop
    }
    kept = _nodes(out)
    for source, targets in add.items():
        unknown = sorted(
            node
            for node in (source, *targets)
            if node not in kept and not node.startswith("tv_aws_users.")
        )
        if unknown:
            raise UnexpectedGraphError(stack, "edge endpoints are absent", unknown)
        edges = out.setdefault(source, [])
        edges.extend(target for target in targets if target not in edges)
    return out


def main(argv: Sequence[str]) -> None:
    """Read the export, prune it, and write the result as JSON."""
    stack, source, destination = argv
    graph = json.loads(Path(source).read_text(encoding="utf-8"))
    pruned = prune(graph, stack)
    Path(destination).write_text(json.dumps(pruned, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{stack}: {len(graph)} -> {len(pruned)} nodes\n")


if __name__ == "__main__":
    main(sys.argv[1:])
