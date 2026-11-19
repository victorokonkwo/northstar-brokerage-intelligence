"""Tests for deterministic identifiers (domain model §4.1)."""

import uuid

import pytest

from northstar.common.ids import NORTHSTAR_NAMESPACE, EntityType, derived_id, source_id


def test_source_ids_are_prefixed_and_deterministic() -> None:
    assert source_id(EntityType.ACCOUNT, 42, 1) == "acc_s42_000001"
    assert source_id(EntityType.CASH_MOVEMENT, 0, 12) == "cm_s0_000012"
    assert source_id(EntityType.EVENT, 7, 1_234_567) == "evt_s7_1234567"


@pytest.mark.parametrize(("seed", "sequence"), [(-1, 1), (1, 0)])
def test_source_id_rejects_bad_inputs(seed: int, sequence: int) -> None:
    with pytest.raises(ValueError, match=r"seed|sequence"):
        source_id(EntityType.ORDER, seed, sequence)


def test_namespace_is_the_documented_uuid5() -> None:
    assert (
        uuid.uuid5(
            uuid.NAMESPACE_URL, "https://github.com/victorokonkwo/northstar-brokerage-intelligence"
        )
        == NORTHSTAR_NAMESPACE
    )


def test_derived_ids_are_stable_and_version_5() -> None:
    first = derived_id("evt_s1_000001", "cash_deposited", 1, 1)
    assert first == derived_id("evt_s1_000001", "cash_deposited", 1, 1)
    assert first.version == 5


def test_derived_ids_do_not_collide_on_separators() -> None:
    assert derived_id("a|b", "c") != derived_id("a", "b|c")
    assert derived_id("1", 1) != derived_id(1, "1")


def test_derived_id_needs_parts() -> None:
    with pytest.raises(ValueError, match="at least one part"):
        derived_id()
