"""Experiment driver + the labeling protocol.

One run = steady state -> start load -> inject a fault at a logged instant ->
observe recovery -> stop. The run manifest records the fault's onset/offset and
the run window, which together are the ground-truth labels aligning the metric
time-series to the failure transition. Every lead-time number traces back to
these labels, so the manifest is the single source of truth for them.
"""

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass

from agent.schema import SCHEMA_VERSION

from .faults import FaultResult

REQUIRED_LABEL_FIELDS = ("run_id", "fault", "fault_onset_epoch",
                         "run_started_epoch", "run_ended_epoch")


@dataclass
class ExperimentConfig:
    steady_s: float = 30.0       # baseline before the fault
    observe_s: float = 60.0      # recovery window after the fault
    load_qps: int = 50
    load_duration_s: float = 150.0
    out_dir: str = "data/runs"


def hey_load_driver(alb_dns: str, qps: int, duration_s: float):
    """Start `hey` in the background against the ALB; returns the Popen handle."""
    return subprocess.Popen([
        "hey", "-z", f"{int(duration_s)}s", "-q", str(qps),
        f"http://{alb_dns}/load?cpu_ms=20&queries=1",
    ])


def validate_manifest(manifest: dict) -> None:
    missing = [f for f in REQUIRED_LABEL_FIELDS if manifest.get(f) is None]
    if missing:
        raise ValueError(f"run manifest missing label fields: {missing}")


def run_experiment(aws, fault, *, alb_dns=None, config: ExperimentConfig | None = None,
                   run_id: str | None = None, fault_kwargs: dict | None = None,
                   load_driver=None) -> dict:
    config = config or ExperimentConfig()
    run_id = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    fault_kwargs = fault_kwargs or {}

    started = time.time()
    load_proc = load_driver(alb_dns, config.load_qps, config.load_duration_s) \
        if (load_driver and alb_dns) else None

    time.sleep(config.steady_s)                      # establish steady state
    result: FaultResult = fault(aws, **fault_kwargs)  # inject + capture labels
    time.sleep(config.observe_s)                     # observe recovery
    ended = time.time()

    if load_proc is not None:
        try:
            load_proc.terminate()
        except Exception:
            pass

    manifest = {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "fault": result.fault,
        "fault_onset_epoch": result.onset_epoch,
        "fault_offset_epoch": result.offset_epoch,
        "fault_detail": result.detail,
        "run_started_epoch": started,
        "run_ended_epoch": ended,
        "alb_dns": alb_dns,
        "config": asdict(config),
    }
    validate_manifest(manifest)

    os.makedirs(config.out_dir, exist_ok=True)
    with open(os.path.join(config.out_dir, f"{run_id}.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def run_batch(aws, fault, n_runs: int, *, alb_dns=None, config=None,
              fault_kwargs=None, load_driver=None, run_prefix=None) -> list[dict]:
    """Run the same experiment many times so results are statistics, not anecdotes."""
    prefix = run_prefix or fault.__name__
    manifests = []
    for i in range(n_runs):
        rid = f"{prefix}-{i:03d}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        manifests.append(run_experiment(
            aws, fault, alb_dns=alb_dns, config=config, run_id=rid,
            fault_kwargs=fault_kwargs, load_driver=load_driver))
    return manifests
