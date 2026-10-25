"""Load the real chaos dataset and run the three-arm analysis on it.

Each fault type has a different user-facing signal (observed in the collected
data): cpu_stress drives CPU to 100%, while the DB/kill faults surface as app
errors (latency and pool barely move — the pool/in-flight gauges read 0 because
gunicorn's workers each keep a private metrics registry). So the "reactive"
baseline arm is chosen per fault: a CPU threshold for cpu_stress, the first
error for the rest. The model arm always uses the full engineered feature set.
"""

import glob
import json
import os

import pandas as pd

from agent.features import build_features, load_raw
from model.model import Detector

from .arms import (
    cloudwatch_alarm_epoch,
    first_sustained,
    highres_threshold_epoch,
    model_detection_epoch,
)
from .stats import bootstrap_ci

RUNS_DIR = "data/runs"
METRICS_DIR = "data/runs/metrics"

# Per-fault user-facing signal. mode "gauge" -> threshold on the metric;
# mode "counter" -> first increase of a cumulative counter (first error).
FAULT_SIGNAL = {
    "cpu_stress": {"metric": "cpu_percent", "threshold": 70.0, "mode": "gauge"},
    "db_block_hard": {"metric": "app_errors_total", "threshold": 0.0, "mode": "counter"},
    "db_reboot": {"metric": "app_errors_total", "threshold": 0.0, "mode": "counter"},
    "instance_kill": {"metric": "app_errors_total", "threshold": 0.0, "mode": "counter"},
}


def _metric_files(run_id: str) -> list[str]:
    return sorted(glob.glob(os.path.join(METRICS_DIR, f"{run_id}__*.jsonl")))


def _counter_onset_epoch(raw, col, k=1):
    vals = raw[col].to_numpy()
    if len(vals) == 0:
        return None
    idx = first_sustained(vals > vals[0], k)
    return None if idx is None else float(raw["ts_epoch"].to_numpy()[idx])


def _cloudwatch_counter_epoch(raw, col, resample_s=60):
    s = (raw.assign(ts=pd.to_datetime(raw["ts_epoch"], unit="s", utc=True))
            .set_index("ts")[col].resample(f"{resample_s}s").max())
    if len(s) == 0:
        return None
    idx = first_sustained((s > s.iloc[0]).to_numpy(), 1)
    return None if idx is None else s.index[idx].timestamp() + resample_s


def reactive_epoch(raw, fault):
    sig = FAULT_SIGNAL[fault]
    if sig["mode"] == "gauge":
        return highres_threshold_epoch(raw, sig["metric"], sig["threshold"])
    return _counter_onset_epoch(raw, sig["metric"])


def cloudwatch_epoch(raw, fault):
    sig = FAULT_SIGNAL[fault]
    if sig["mode"] == "gauge":
        return cloudwatch_alarm_epoch(raw, sig["metric"], sig["threshold"])
    return _cloudwatch_counter_epoch(raw, sig["metric"])


def train_on_baseline(contamination=0.02) -> Detector:
    files = glob.glob(os.path.join(METRICS_DIR, "baseline-*.jsonl"))
    feats = pd.concat([build_features(f) for f in files], ignore_index=True)
    return Detector(contamination=contamination).fit(feats)


def _fault_runs() -> list[tuple[str, str]]:
    runs = []
    for manifest_path in sorted(glob.glob(os.path.join(RUNS_DIR, "*.json"))):
        m = json.load(open(manifest_path, encoding="utf-8"))
        if m.get("fault") in FAULT_SIGNAL:
            runs.append((m["run_id"], m["fault"]))
    return runs


def analyze_real(detector: Detector) -> dict:
    per_fault: dict[str, list[float]] = {f: [] for f in FAULT_SIGNAL}
    sampling_gain: list[float] = []
    all_leads: list[float] = []

    for run_id, fault in _fault_runs():
        for path in _metric_files(run_id):
            raw = load_raw(path)
            feats = build_features(path)
            model_t = model_detection_epoch(detector, feats)
            react_t = reactive_epoch(raw, fault)
            cw_t = cloudwatch_epoch(raw, fault)

            if model_t is not None and react_t is not None:
                lead = react_t - model_t          # model earlier than reactive baseline
                per_fault[fault].append(lead)
                all_leads.append(lead)
            if react_t is not None and cw_t is not None:
                sampling_gain.append(cw_t - react_t)

    summary = {fault: bootstrap_ci(leads) for fault, leads in per_fault.items()}
    summary["overall_lead"] = bootstrap_ci(all_leads)
    summary["sampling_gain"] = bootstrap_ci(sampling_gain)
    return summary


def false_positive_rate(detector: Detector) -> float:
    files = glob.glob(os.path.join(METRICS_DIR, "baseline-*.jsonl"))
    feats = pd.concat([build_features(f) for f in files], ignore_index=True)
    import numpy as np
    return round(float(np.mean(detector.is_anomaly(feats))), 4)


def main() -> None:
    detector = train_on_baseline()
    summary = analyze_real(detector)
    fp = false_positive_rate(detector)

    def line(label, stat):
        if stat["mean"] is None:
            return f"  {label:22}: n/a (n=0)"
        return (f"  {label:22}: {stat['mean']:+.1f}s "
                f"[{stat['ci_low']:+.1f}, {stat['ci_high']:+.1f}]  (n={stat['n']})")

    print("Real chaos dataset — model lead time over reactive baseline")
    for fault in FAULT_SIGNAL:
        print(line(fault, summary[fault]))
    print(line("OVERALL", summary["overall_lead"]))
    print(line("sampling gain (hi-res vs CW)", summary["sampling_gain"]))
    print(f"  model FP rate (baseline): {fp}")


if __name__ == "__main__":
    main()
