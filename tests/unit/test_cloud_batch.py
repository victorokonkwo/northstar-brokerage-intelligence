"""The cloud daily close (roadmap 4G) is consistent with the CLI and the Airflow DAG.

Nothing here touches AWS. The state machine template, the step script, and the
task definition's environment are three files that must agree: these tests
fail if a step runs a command the CLI doesn't accept, if the steps leave the
DAG's order, if a step can continue after a non-zero exit, or if the script
reads a variable that nothing sets.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from northstar.application.cli import build_parser

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "orchestration" / "cloud" / "run_step.sh"
STACK = ROOT / "infrastructure" / "terraform" / "batch"
# The DAG's tasks, in order; the cloud runs ingest and load as one task.
DAG_ORDER = [
    "check_business_day",
    "ingest_and_load",
    "dbt_build",
    "reconcile",
    "dbt_controls",
    "publish_controls",
    "publish",
]
SET_BY_STATE_MACHINE = {"NBI_BUSINESS_DATE", "NBI_RUN"}


def _definition() -> dict[str, Any]:
    text = (STACK / "state_machine.json.tftpl").read_text(encoding="utf-8")
    values = {"subnets_json": '["subnet-a", "subnet-b"]'}
    rendered = re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), f"<{m.group(1)}>"), text)
    definition: dict[str, Any] = json.loads(rendered)
    return definition


def _script_steps() -> dict[str, str]:
    """Each ``case`` branch of the step script, by step name."""
    text = SCRIPT.read_text(encoding="utf-8")
    body = text[text.index("case ${1:-} in") :]
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^  ([a-z_]+)\)\n(.*?)^    ;;", body, re.MULTILINE | re.DOTALL)
    }


def test_the_step_script_is_valid_shell() -> None:
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)  # noqa: S603, S607 - fixed arguments


def test_the_script_has_exactly_the_dag_steps() -> None:
    assert list(_script_steps()) == DAG_ORDER


@pytest.mark.parametrize("step", DAG_ORDER)
def test_every_northstar_command_parses(step: str) -> None:
    lines = _script_steps()[step].replace("\\\n", " ").splitlines()
    commands = [line.strip() for line in lines if line.strip().startswith("northstar ")]
    if step.startswith("dbt_"):
        assert commands == []  # dbt runs through dbt_ol, which archives with northstar
    for command in commands:
        sample = re.sub(r'"\$(\w+)"', r"sample_\1", command.split(" >&2")[0])
        sample = sample.replace("sample_NBI_BUSINESS_DATE", "2026-03-02")
        build_parser().parse_args(shlex.split(sample)[1:])


def test_the_state_machine_runs_the_steps_in_dag_order() -> None:
    states = _definition()["States"]
    order, name = [], _definition()["StartAt"]
    while states[name]["Type"] != "Succeed":
        state = states[name]
        if state["Type"] == "Task" and state["Resource"].endswith("ecs:runTask.sync"):
            (override,) = state["Arguments"]["Overrides"]["ContainerOverrides"]
            order.append(override["Command"][0])
        # A Choice's first branch is the exit-code-0 one.
        name = state["Choices"][0]["Next"] if state["Type"] == "Choice" else state["Next"]
    assert order == DAG_ORDER


def test_a_step_continues_only_after_exit_code_zero() -> None:
    states = _definition()["States"]
    tasks = {k: v for k, v in states.items() if v.get("Resource", "").endswith("runTask.sync")}
    assert len(tasks) == len(DAG_ORDER)
    for name, task in tasks.items():
        assert task["Catch"][0]["ErrorEquals"] == ["States.ALL"]
        assert task["Catch"][0]["Next"] == "NotifyFailure"
        check = states[task["Next"]]
        assert check["Type"] == "Choice"
        assert check["Default"] == "NotifyFailure"
        conditions = {c["Condition"]: c["Next"] for c in check["Choices"]}
        assert "{% $states.input.exit_code = 0 %}" in conditions
        others = set(conditions) - {"{% $states.input.exit_code = 0 %}"}
        if name == "CheckBusinessDay":
            assert others == {"{% $states.input.exit_code = 99 %}"}
            assert states[conditions["{% $states.input.exit_code = 99 %}"]]["Type"] == "Succeed"
        else:
            assert others == set()
    assert states["NotifyFailure"]["Next"] == "Failed"
    assert states["Failed"]["Type"] == "Fail"


def test_every_variable_the_script_reads_is_set() -> None:
    read = set(re.findall(r"\$\{?(NBI_[A-Z_]+)", SCRIPT.read_text(encoding="utf-8")))
    ecs = (STACK / "ecs.tf").read_text(encoding="utf-8")
    environment = ecs[ecs.index("environment = {") : ecs.index("}", ecs.index("environment = {"))]
    task_definition = set(re.findall(r"^\s+(NBI_[A-Z_]+)\s+=", environment, re.MULTILINE))
    overrides = {
        env["Name"]
        for state in _definition()["States"].values()
        for override in state.get("Arguments", {})
        .get("Overrides", {})
        .get("ContainerOverrides", [])
        for env in override["Environment"]
    }
    assert overrides == SET_BY_STATE_MACHINE
    assert read <= task_definition | overrides, read - task_definition - overrides
