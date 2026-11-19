"""Use case: generate a dataset's canonical events file (``northstar simulate``)."""

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from opentelemetry import trace

from northstar.application.policies import load_policies
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events
from northstar.simulation.generator import DatasetConfig, generate_dataset


@dataclass(frozen=True, slots=True)
class SimulatedDataset:
    """Where the canonical events were written, with their counts and SHA-256."""

    dataset_id: str
    seed: int
    events_path: Path
    events_sha256: str
    counts: dict[str, int]


def simulate_dataset(
    config_dir: Path, dataset_path: Path, seed: int, out_dir: Path
) -> SimulatedDataset:
    """Generate ``<out_dir>/<dataset_id>/seed=<seed>/canonical/events.jsonl``.

    The same config, seed, and policy versions always give the same bytes.

    Raises:
        ConfigurationError: A policy or dataset file is missing or invalid.
        ScenarioError: The generator produced an action the brokerage refuses.
    """
    with trace.get_tracer(__name__).start_as_current_span("northstar.simulate") as span:
        config = load_policy(dataset_path, DatasetConfig)
        events = generate_dataset(config, seed, load_policies(config_dir))
        path = out_dir / config.dataset_id / f"seed={seed}" / "canonical" / "events.jsonl"
        write_events(path, events)
        result = SimulatedDataset(
            dataset_id=config.dataset_id,
            seed=seed,
            events_path=path,
            events_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            counts=dict(sorted(Counter(str(e.event_type) for e in events).items())),
        )
        span.set_attributes(
            {
                "northstar.dataset_id": result.dataset_id,
                "northstar.seed": seed,
                "northstar.events.count": len(events),
                "northstar.events.sha256": result.events_sha256,
            }
        )
        logging.getLogger(__name__).info(
            "simulated %d events", len(events), extra={"job": "simulate", "seed": seed}
        )
        return result
