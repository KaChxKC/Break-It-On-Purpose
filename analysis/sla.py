"""Lead time to the user-facing SLA breach — the honest "faster" metric.

For a gradual fault, a conventional alarm fires when the SLA metric crosses its
line (p95 latency > 500ms, or error rate > 1%). The model, watching the trend in
the precursor features, flags the anomaly while the metric is still climbing.
Lead time = SLA-breach epoch - model-detection epoch; positive means the model
warned before users were affected.
"""

import glob
import json

import pandas as pd

from agent.features import build_features, load_raw

from .arms import first_sustained, model_detection_epoch
from .stats import bootstrap_ci

SLA_LATENCY_MS = 500.0
SLA_ERROR_RATE = 0.01
GRADUAL_FAULTS = ("ramp_cpu", "pool_fill", "mem_leak")


def _error_rate(raw):
    return raw["app_errors_total"].diff() / raw["ts_epoch"].diff()


def sla_breach_epoch(raw, latency_ms=SLA_LATENCY_MS, error_rate=SLA_ERROR_RATE, k=3):
    """First epoch the system breaches the SLA (latency OR error rate)."""
    ts = raw["ts_epoch"].to_numpy()
    candidates = []
    if "app_latency_p95_ms" in raw.columns:
        idx = first_sustained(raw["app_latency_p95_ms"].to_numpy() >= latency_ms, k)
        if idx is not None:
            candidates.append(ts[idx])
    if "app_errors_total" in raw.columns:
        idx = first_sustained(_error_rate(raw).fillna(0).to_numpy() >= error_rate, k)
        if idx is not None:
            candidates.append(ts[idx])
    return float(min(candidates)) if candidates else None


def model_lead_to_sla(detector, raw, feats, k=2, latency_ms=SLA_LATENCY_MS,
                      error_rate=SLA_ERROR_RATE):
    breach = sla_breach_epoch(raw, latency_ms, error_rate)
    model_t = model_detection_epoch(detector, feats, k)
    if breach is None or model_t is None:
        return None
    return breach - model_t


def analyze_gradual(detector, runs, k=2, latency_ms=SLA_LATENCY_MS):
    per_fault: dict[str, list[float]] = {}
    all_leads: list[float] = []
    for run in runs:
        lead = model_lead_to_sla(detector, run["raw"], run["feats"], k, latency_ms)
        if lead is not None:
            per_fault.setdefault(run["fault"], []).append(lead)
            all_leads.append(lead)
    summary = {f: bootstrap_ci(v) for f, v in per_fault.items()}
    summary["overall"] = bootstrap_ci(all_leads)
    return summary


def main():
    from .realdata import _metric_files, train_on_baseline

    runs = []
    for manifest_path in sorted(glob.glob("data/runs/*.json")):
        m = json.load(open(manifest_path, encoding="utf-8"))
        if m.get("fault") in GRADUAL_FAULTS:
            for path in _metric_files(m["run_id"]):
                runs.append({"raw": load_raw(path), "feats": build_features(path),
                             "fault": m["fault"]})
    if not runs:
        print("No gradual runs yet — run `python -m chaos.gradual --alb <dns>` first.")
        return

    summary = analyze_gradual(train_on_baseline(), runs)

    def line(label, stat):
        if stat["mean"] is None:
            return f"  {label:12}: n/a (n=0)"
        return (f"  {label:12}: model warns {stat['mean']:.1f}s before SLA "
                f"[{stat['ci_low']:.1f}, {stat['ci_high']:.1f}] (n={stat['n']})")

    print("Lead time to SLA breach (gradual faults)")
    for fault in GRADUAL_FAULTS:
        if fault in summary:
            print(line(fault, summary[fault]))
    print(line("OVERALL", summary["overall"]))


if __name__ == "__main__":
    main()
