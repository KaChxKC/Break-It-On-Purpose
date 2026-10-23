"""Full offline analysis: run the three arms across many runs, aggregate the
lead time (mean ± 95% CI), false-positive rate, RTO and ablation, and render the
figures. Works on synthetic runs now; swap `_make_run` for a loader over the
real S3/`data/runs` dataset once chaos runs have been collected.
"""

import argparse
import os
import tempfile

from agent.features import build_features, load_raw
from agent.synthetic import generate_run, write_jsonl
from model.model import Detector

from . import plots
from .arms import DEFAULT_METRIC, DEFAULT_THRESHOLD, run_lead_times, three_arm_epochs
from .stats import ablation, bootstrap_ci, model_false_positive_rate, rto, threshold_fires

ABLATION_THRESHOLDS = {
    "cpu_percent": 70.0,
    "mem_percent": 70.0,
    "app_latency_p95_ms": 100.0,
    "app_in_flight": 15,
    "app_pool_utilization": 0.6,
}

_LEAD_KEYS = ("model_over_highres", "highres_over_cloudwatch", "model_over_cloudwatch")


def analyze_runs(detector, runs, metric=DEFAULT_METRIC, threshold=DEFAULT_THRESHOLD) -> dict:
    leads = {k: [] for k in _LEAD_KEYS}
    rtos, beats = [], 0
    first_ablation = None
    for run in runs:
        lt = run_lead_times(run["raw"], run["feats"], detector, metric, threshold)
        for k in _LEAD_KEYS:
            leads[k].append(lt[k])
        recovery = rto(run["raw"], run["onset"], metric, threshold)
        if recovery is not None:
            rtos.append(recovery)
        ab = ablation(run["raw"], run["feats"], detector, ABLATION_THRESHOLDS)
        first_ablation = first_ablation or ab
        beats += int(ab["model_beats_best_single"])

    summary = {k: bootstrap_ci(leads[k]) for k in _LEAD_KEYS}
    summary["rto"] = bootstrap_ci(rtos)
    summary["model_beats_best_single"] = f"{beats}/{len(runs)}"
    summary["ablation_example"] = first_ablation
    return summary


def _features_and_raw(rows):
    path = os.path.join(tempfile.gettempdir(), f"an_{id(rows)}.jsonl")
    write_jsonl(rows, path)
    try:
        return load_raw(path), build_features(path)
    finally:
        os.remove(path)


def _make_run(seed):
    rows, onset = generate_run(n_normal=100, n_fault=60, seed=seed)
    raw, feats = _features_and_raw(rows)
    return {"raw": raw, "feats": feats, "onset": onset, "fault": "cpu_stress"}


def run_synthetic_analysis(n_runs: int = 8, out_dir: str = "analysis/figures") -> dict:
    normal_rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
    _, normal_feats = _features_and_raw(normal_rows)
    detector = Detector(contamination=0.02).fit(normal_feats)

    runs = [_make_run(1000 + i) for i in range(n_runs)]
    summary = analyze_runs(detector, runs)

    quiet_rows, _ = generate_run(n_normal=400, n_fault=0, seed=777)
    quiet_raw, quiet_feats = _features_and_raw(quiet_rows)
    summary["model_false_positive_rate"] = round(model_false_positive_rate(detector, quiet_feats), 4)
    summary["threshold_false_positive"] = threshold_fires(quiet_raw, DEFAULT_METRIC, DEFAULT_THRESHOLD)

    os.makedirs(out_dir, exist_ok=True)
    plots.lead_time_bar(summary, os.path.join(out_dir, "lead_time.png"))
    example = runs[0]
    plots.detection_timeline(
        example["raw"],
        three_arm_epochs(example["raw"], example["feats"], detector),
        example["onset"], DEFAULT_METRIC,
        os.path.join(out_dir, "timeline.png"),
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Three-arm analysis on synthetic runs")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--out", default="analysis/figures")
    args = parser.parse_args()
    s = run_synthetic_analysis(n_runs=args.runs, out_dir=args.out)

    def fmt(stat):
        return "n/a" if stat["mean"] is None else f"{stat['mean']:.1f}s"

    hr = s["model_over_highres"]
    print("Three-arm analysis")
    print(f"  lead time (model vs high-res): {fmt(hr)}"
          + (f" [{hr['ci_low']:.1f}, {hr['ci_high']:.1f}] (n={hr['n']})" if hr["mean"] is not None else ""))
    print(f"  sampling gain (high-res vs CW): {fmt(s['highres_over_cloudwatch'])}")
    print(f"  model FP rate (quiet window)  : {s['model_false_positive_rate']}")
    print(f"  mean RTO                       : {fmt(s['rto'])}")
    print(f"  model beats best single metric : {s['model_beats_best_single']}")
    print(f"  figures -> {args.out}/")


if __name__ == "__main__":
    main()
