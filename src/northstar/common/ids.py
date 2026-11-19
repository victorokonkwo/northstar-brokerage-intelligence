"""Deterministic identifiers (domain model §4.1).

- **Source entities** get readable prefixed IDs built from the dataset seed and
  a per-type sequence number, e.g. ``acc_s42_000001``. The same seed always
  produces the same IDs.
- **Derived records** (journal batches and lines) get UUIDv5 IDs computed from
  their causal inputs, so re-posting the same event under the same rule
  version reproduces the same IDs, which makes posting idempotent.
"""

import json
import uuid
from enum import StrEnum

NORTHSTAR_NAMESPACE = uuid.UUID("dce6c8bb-70dd-55e8-9353-dda5e8539deb")
"""UUIDv5 namespace for derived IDs.

It is ``uuid5(NAMESPACE_URL, "https://github.com/victorokonkwo/northstar-brokerage-intelligence")``,
fixed here so that derived IDs never change between releases.
"""


class EntityType(StrEnum):
    """Source entity kinds and their ID prefixes."""

    ACCOUNT = "acc"
    INSTRUMENT = "ins"
    ORDER = "ord"
    EXECUTION = "exe"
    CASH_MOVEMENT = "cm"
    FX_CONVERSION = "fxc"
    SETTLEMENT = "stl"
    EVENT = "evt"


def source_id(entity: EntityType, seed: int, sequence: int) -> str:
    """Return the deterministic ID of the ``sequence``-th ``entity`` in a dataset.

    Raises:
        ValueError: ``seed`` is negative or ``sequence`` is less than 1.
    """
    if seed < 0:
        msg = f"seed must be non-negative, got {seed}"
        raise ValueError(msg)
    if sequence < 1:
        msg = f"sequence must be at least 1, got {sequence}"
        raise ValueError(msg)
    return f"{entity}_s{seed}_{sequence:06d}"


def derived_id(*parts: str | int) -> uuid.UUID:
    """Return a UUIDv5 over ``parts``.

    The parts are encoded as a JSON array, so ``("a|b", "c")`` and
    ``("a", "b|c")`` can never collide.

    Raises:
        ValueError: No parts were given.
    """
    if not parts:
        msg = "derived_id needs at least one part"
        raise ValueError(msg)
    return uuid.uuid5(NORTHSTAR_NAMESPACE, json.dumps(list(parts), separators=(",", ":")))
