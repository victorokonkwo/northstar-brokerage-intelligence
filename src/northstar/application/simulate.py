"""Use case: generate a dataset (``northstar simulate``).

Writes, under ``<out>/<dataset_id>/seed=<seed>/``:

- ``canonical/events.jsonl``: the ground-truth events
- ``external/<set>.jsonl``: what the outside parties report (FR-SIM-02)
- ``defects/manifest.jsonl``: the injected defects (FR-SIM-03), when a defect
  config is given. Without one, the external records are exact.

The same inputs and policy versions always give the same bytes in every file.
"""

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from opentelemetry import trace

from northstar.application.policies import load_policies
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events, write_records
from northstar.simulation.defects import DefectConfig, DefectEntry, inject_defects
from northstar.simulation.engine import LEDGER_CALENDAR_MARKET
from northstar.simulation.external import derive_external_records
from northstar.simulation.generator import DatasetConfig, generate_dataset


@dataclass(frozen=True, slots=True)
class SimulatedDataset:
    """Where the dataset was written, with counts and a SHA-256 per file."""

    dataset_id: str
    seed: int
    root: Path
    counts: dict[str, int]
    external_counts: dict[str, int]
    defect_counts: dict[str, int]
    file_sha256: dict[str, str]

    @property
    def events_path(self) -> Path:
        """The canonical events file."""
        return self.root / "canonical" / "events.jsonl"


def simulate_dataset(
    config_dir: Path,
    dataset_path: Path,
    seed: int,
    out_dir: Path,
    defects_path: Path | None = None,
) -> SimulatedDataset:
    """Generate the dataset's canonical events, external records, and defect manifest.

    Raises:
        ConfigurationError: A policy, dataset, or defect file is missing or invalid.
        ScenarioError: The generator produced an action the brokerage refuses.
    """
    with trace.get_tracer(__name__).start_as_current_span("northstar.simulate") as span:
        config = load_policy(dataset_path, DatasetConfig)
        policies = load_policies(config_dir)
        events = generate_dataset(config, seed, policies)
        root = out_dir / config.dataset_id / f"seed={seed}"
        write_events(root / "canonical" / "events.jsonl", events)
        ledger = policies.calendars[LEDGER_CALENDAR_MARKET]
        external = derive_external_records(events, seed, ledger)
        manifest: tuple[DefectEntry, ...] = ()
        if defects_path is not None:
            defect_config = load_policy(defects_path, DefectConfig)
            injected = inject_defects(external, events, defect_config, seed, ledger)
            external, manifest = injected.records, injected.manifest
            write_records(root / "defects" / "manifest.jsonl", manifest)
        for name, records in external.by_name().items():
            write_records(root / "external" / f"{name}.jsonl", records)
        result = SimulatedDataset(
            dataset_id=config.dataset_id,
            seed=seed,
            root=root,
            counts=dict(sorted(Counter(str(e.event_type) for e in events).items())),
            external_counts={n: len(r) for n, r in external.by_name().items()},
            defect_counts=dict(sorted(Counter(str(d.defect_type) for d in manifest).items())),
            file_sha256={
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*.jsonl"))
            },
        )
        span.set_attributes(
            {
                "northstar.dataset_id": result.dataset_id,
                "northstar.seed": seed,
                "northstar.events.count": len(events),
                "northstar.defects.count": len(manifest),
            }
        )
        logging.getLogger(__name__).info(
            "simulated %d events, %d defects",
            len(events),
            len(manifest),
            extra={"job": "simulate", "seed": seed},
        )
        return result
