"""Tests for the Sprint 1 slice use case that need no database."""

import hashlib
from pathlib import Path

import pytest

from northstar.application.cli import build_parser
from northstar.application.sprint1 import prepare_slice

from factories import CONFIG

SCENARIO = CONFIG / "simulation" / "sprint1_scenario.yaml"


def test_prepare_slice_writes_the_events_file_and_posts_the_journal(tmp_path: Path) -> None:
    prepared = prepare_slice(CONFIG, SCENARIO, 42, tmp_path)
    assert prepared.events_path == tmp_path / "sprint1-cad-buy" / "seed=42" / "events.jsonl"
    assert len(prepared.events) == 7  # instrument_listed + the 6 slice events
    assert len(prepared.batches) == 4
    assert prepared.events_sha256 == hashlib.sha256(prepared.events_path.read_bytes()).hexdigest()


def test_same_inputs_give_the_same_file_in_another_directory(tmp_path: Path) -> None:
    first = prepare_slice(CONFIG, SCENARIO, 42, tmp_path / "a")
    second = prepare_slice(CONFIG, SCENARIO, 42, tmp_path / "b")
    assert first.events_sha256 == second.events_sha256
    assert first.batches == second.batches


def test_another_seed_gives_another_file(tmp_path: Path) -> None:
    assert (
        prepare_slice(CONFIG, SCENARIO, 42, tmp_path).events_sha256
        != prepare_slice(CONFIG, SCENARIO, 7, tmp_path).events_sha256
    )


class TestParser:
    def test_run_slice_requires_scenario_seed_and_out(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run-slice", "--seed", "42"])

    def test_run_slice_arguments(self) -> None:
        args = build_parser().parse_args(
            ["run-slice", "--scenario", "s.yaml", "--seed", "42", "--out", "data/generated"]
        )
        assert (args.scenario, args.seed, args.out, args.config) == (
            Path("s.yaml"),
            42,
            Path("data/generated"),
            Path("config"),
        )

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])
