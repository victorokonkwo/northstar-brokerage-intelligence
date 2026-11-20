"""The synthetic market history before the window (roadmap 5A)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from northstar.application.simulate import simulate_dataset
from northstar.common.money import Currency
from northstar.domain.settlement import AssetClass
from northstar.infrastructure.config_loader import load_policy
from northstar.ingestion.sources import Accepted, classify_record
from northstar.simulation.generator import DatasetConfig
from northstar.simulation.history import (
    HistorySpec,
    backward_path,
    generate_history,
    history_days,
)
from northstar.simulation.market import PRICE_TICK

from factories import CONFIG

DEV = CONFIG / "simulation" / "datasets" / "dev.yaml"
START, END = date(2025, 1, 2), date(2025, 12, 31)
SPECS = [
    HistorySpec("ins_1", Currency.CAD, AssetClass.EQUITY, Decimal("52.40"), Decimal("0.018")),
    HistorySpec("ins_2", Currency.USD, AssetClass.EQUITY, Decimal("9.80"), Decimal("0.035")),
    HistorySpec("ins_3", Currency.USD, AssetClass.OPTION, Decimal("2.15"), Decimal("0.060")),
]


def test_the_history_calendar_is_every_weekday() -> None:
    days = history_days(START, END)
    assert all(d.weekday() < 5 for d in days)
    assert (days[0], days[-1]) == (date(2025, 1, 2), date(2025, 12, 31))
    # An independent count of Monday-Friday days (end exclusive in numpy).
    assert len(days) == np.busday_count(START, date(2026, 1, 1))


def test_a_backward_path_ends_at_its_anchor() -> None:
    rng = np.random.Generator(np.random.PCG64(7))
    days = history_days(START, END)
    path = backward_path(rng, Decimal("52.40"), Decimal("0.018"), days, PRICE_TICK)
    assert list(path) == days
    assert path[days[-1]] == Decimal("52.40")
    assert all(v >= PRICE_TICK and v == v.quantize(PRICE_TICK) for v in path.values())
    assert len(set(path.values())) > 100  # it moves


def test_history_covers_equities_and_fx_and_ends_at_the_anchors() -> None:
    history = generate_history(SPECS, Decimal("1.3700"), Decimal("0.0040"), START, END, 42)
    instruments = {r.instrument_id for r in history.price_history}
    assert instruments == {"ins_1", "ins_2"}  # no option: the risk model reprices them
    last = {r.instrument_id: r.close_price for r in history.price_history if r.price_date == END}
    assert last == {"ins_1": Decimal("52.40"), "ins_2": Decimal("9.80")}
    assert history.fx_rate_history[-1].rate == Decimal("1.3700")
    assert history.fx_rate_history[-1].rate_date == END
    ids = [r.record_id for r in history.price_history]
    assert ids == [f"pxh_s42_{n:06d}" for n in range(1, len(ids) + 1)]  # consecutive, unique


def test_history_is_deterministic_and_seeded() -> None:
    def draw(seed: int) -> list[Decimal]:
        history = generate_history(SPECS, Decimal("1.37"), Decimal("0.004"), START, END, seed)
        return [r.close_price for r in history.price_history]

    assert draw(42) == draw(42)
    assert draw(42) != draw(43)


def test_the_history_must_end_before_the_fx_paths_first_day() -> None:
    config = load_policy(DEV, DatasetConfig).model_dump()
    config["history"] = {"start": "2025-01-02", "end": "2026-01-01"}  # the FX path's first day
    with pytest.raises(ValidationError, match="history must"):
        DatasetConfig.model_validate(config)


def test_adding_history_changes_nothing_else_in_the_dataset(tmp_path: Path) -> None:
    with_history = simulate_dataset(CONFIG, DEV, 42, tmp_path / "with")
    plain = tmp_path / "plain.yaml"
    plain.write_text(
        "\n".join(line for line in DEV.read_text().splitlines() if not line.startswith("history:"))
    )
    without = simulate_dataset(CONFIG, plain, 42, tmp_path / "without")
    extra = {"external/price_history.jsonl", "external/fx_rate_history.jsonl"}
    assert set(with_history.file_sha256) - set(without.file_sha256) == extra
    for name, digest in without.file_sha256.items():
        assert with_history.file_sha256[name] == digest, name
    line = (with_history.root / "external" / "price_history.jsonl").read_text().splitlines()[0]
    accepted = classify_record("price_history", line)
    assert isinstance(accepted, Accepted)
    assert (accepted.domain, accepted.event_date) == ("market_data", date(2025, 1, 2))
