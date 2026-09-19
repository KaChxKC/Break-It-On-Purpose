"""Offline validation: does the model warn *before* a naive threshold would?

We train on a normal run, then on synthetic failure runs compare when the
detector first raises a sustained anomaly against when a raw metric first
breaches a static threshold. The gap is the lead time — the core claim of the
project, checked here for free before any AWS spend. (The rigorous three-arm
comparison on real chaos data comes in Phase 7.)
"""

import os
import tempfile

import numpy as np

from agent.features import build_features
from agent.synthetic import generate_run, write_jsonl

from .model import Detector


def first_sustained(mask, k: int = 3) -> int | None:
    """Index where the first run of `k` consecutive True values begins.

    Requiring a sustained run debounces single-sample flicker so a lone false
    positive can't masquerade as an early warning.
    """
    mask = np.asarray(mask, dtype=bool)
    run = 0
    for i, val in enumerate(mask):
        run = run + 1 if val else 0
        if run >= k:
            return i - k + 1
    return None


def evaluate_run(detector, feats, fault_onset, threshold_col="app_latency_p95_ms",
                 threshold=100.0, k=3) -> dict:
    ts = feats["ts_epoch"].to_numpy()
    model_idx = first_sustained(detector.is_anomaly(feats), k)
    thr_idx = first_sustained(feats[threshold_col].to_numpy() >= threshold, k)

    model_t = float(ts[model_idx]) if model_idx is not None else None
    thr_t = float(ts[thr_idx]) if thr_idx is not None else None
    lead = thr_t - model_t if (model_t is not None and thr_t is not None) else None
    return {
        "fault_onset_epoch": fault_onset,
        "model_warn_epoch": model_t,
        "threshold_breach_epoch": thr_t,
        "lead_time_sec": lead,
        "model_warns_before_threshold": bool(lead is not None and lead > 0),
    }


def false_positive_rate(detector, feats) -> float:
    return float(np.mean(detector.is_anomaly(feats)))


def _features_for(rows):
    path = os.path.join(tempfile.gettempdir(), f"biop_val_{os.getpid()}_{id(rows)}.jsonl")
    write_jsonl(rows, path)
    try:
        return build_features(path)
    finally:
        os.remove(path)


def run_offline_validation(n_runs: int = 8, contamination: float = 0.02) -> dict:
    normal_rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
    detector = Detector(contamination=contamination).fit(_features_for(normal_rows))

    # held-out normal run for false-positive rate
    heldout_normal, _ = generate_run(n_normal=200, n_fault=0, seed=999)
    fpr = false_positive_rate(detector, _features_for(heldout_normal))

    leads = []
    for s in range(n_runs):
        rows, onset = generate_run(n_normal=100, n_fault=60, seed=1000 + s)
        result = evaluate_run(detector, _features_for(rows), onset)
        if result["lead_time_sec"] is not None:
            leads.append(result["lead_time_sec"])

    leads = np.array(leads, dtype=float)
    return {
        "runs": n_runs,
        "false_positive_rate": round(fpr, 4),
        "lead_time_mean_sec": round(float(leads.mean()), 2) if leads.size else None,
        "lead_time_std_sec": round(float(leads.std(ddof=1)), 2) if leads.size > 1 else 0.0,
        "runs_warned_before_threshold": int((leads > 0).sum()),
    }


def main() -> None:
    summary = run_offline_validation()
    print("Offline validation (synthetic failure runs)")
    print(f"  runs                       : {summary['runs']}")
    print(f"  false-positive rate (normal): {summary['false_positive_rate']}")
    print(f"  lead time  mean +/- std (s) : {summary['lead_time_mean_sec']} +/- {summary['lead_time_std_sec']}")
    print(f"  runs warned before threshold: {summary['runs_warned_before_threshold']}/{summary['runs']}")


if __name__ == "__main__":
    main()
