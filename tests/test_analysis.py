import os
import tempfile

import pandas as pd

from agent.features import build_features, load_raw
from agent.synthetic import generate_run, write_jsonl
from analysis.arms import run_lead_times, three_arm_epochs
from analysis.report import run_synthetic_analysis
from analysis.stats import ablation, bootstrap_ci, rto
from model.model import Detector


def _raw_feats(rows):
    path = os.path.join(tempfile.gettempdir(), f"an_{id(rows)}.jsonl")
    write_jsonl(rows, path)
    try:
        return load_raw(path), build_features(path)
    finally:
        os.remove(path)


def _detector():
    rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
    _, feats = _raw_feats(rows)
    return Detector(contamination=0.02).fit(feats)


def test_arms_fire_and_order(  ):
    det = _detector()
    rows, _ = generate_run(n_normal=100, n_fault=120, seed=3)
    raw, feats = _raw_feats(rows)
    epochs = three_arm_epochs(raw, feats, det)
    assert epochs["highres"] is not None
    assert epochs["model"] is not None
    if epochs["cloudwatch"] is not None:
        assert epochs["highres"] <= epochs["cloudwatch"]  # finer resolution never later


def test_model_warns_before_highres_threshold():
    det = _detector()
    rows, _ = generate_run(n_normal=100, n_fault=120, seed=4)
    raw, feats = _raw_feats(rows)
    lt = run_lead_times(raw, feats, det)
    assert lt["model_over_highres"] is not None
    assert lt["model_over_highres"] > 0        # model is earlier than the raw threshold


def test_bootstrap_ci():
    s = bootstrap_ci([10, 10, 10, 10])
    assert s["mean"] == 10 and s["ci_low"] <= 10 <= s["ci_high"]
    assert bootstrap_ci([None, 5, None, 7])["n"] == 2


def test_rto_on_recovering_series():
    raw = pd.DataFrame({"ts_epoch": list(range(20)),
                        "cpu_percent": [80] * 5 + [20] * 15})
    assert rto(raw, onset_epoch=0, metric="cpu_percent", threshold=70, recover_k=3) == 5.0


def test_ablation_structure():
    det = _detector()
    rows, _ = generate_run(n_normal=100, n_fault=120, seed=5)
    raw, feats = _raw_feats(rows)
    ab = ablation(raw, feats, det, {"cpu_percent": 70.0, "app_latency_p95_ms": 100.0})
    assert "earliest_metric" in ab and "model_epoch" in ab


def test_run_synthetic_analysis(tmp_path):
    s = run_synthetic_analysis(n_runs=4, out_dir=str(tmp_path))
    assert s["model_over_highres"]["mean"] is not None
    assert s["model_over_highres"]["mean"] > 0
    assert s["model_false_positive_rate"] < 0.10
    assert (tmp_path / "lead_time.png").exists()
    assert (tmp_path / "timeline.png").exists()
