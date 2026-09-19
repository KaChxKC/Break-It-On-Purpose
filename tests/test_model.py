import os
import tempfile

import numpy as np

from agent.features import build_features
from agent.synthetic import generate_run, write_jsonl
from model.model import Detector
from model.validate import evaluate_run, false_positive_rate, first_sustained


def _features(rows):
    path = os.path.join(tempfile.gettempdir(), f"biop_test_{id(rows)}.jsonl")
    write_jsonl(rows, path)
    try:
        return build_features(path)
    finally:
        os.remove(path)


def _trained_detector():
    normal_rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
    return Detector(contamination=0.02).fit(_features(normal_rows))


def test_first_sustained():
    assert first_sustained([0, 0, 1, 1, 1, 0], k=3) == 2
    assert first_sustained([0, 1, 0, 1, 0], k=2) is None
    assert first_sustained([1, 1], k=2) == 0


def test_low_false_positive_on_normal():
    det = _trained_detector()
    heldout, _ = generate_run(n_normal=200, n_fault=0, seed=42)
    assert false_positive_rate(det, _features(heldout)) < 0.10


def test_detects_fault_region():
    det = _trained_detector()
    rows, onset = generate_run(n_normal=100, n_fault=60, seed=7)
    feats = _features(rows)
    flags = det.is_anomaly(feats)
    is_fault = (feats["ts_epoch"] >= onset).to_numpy()
    assert flags[is_fault].mean() > 0.7          # most of the fault flagged
    assert flags[~is_fault].mean() < 0.10        # few false positives before it


def test_warns_before_threshold():
    det = _trained_detector()
    rows, onset = generate_run(n_normal=100, n_fault=60, seed=8)
    result = evaluate_run(det, _features(rows), onset)
    assert result["model_warns_before_threshold"] is True
    assert result["lead_time_sec"] > 0
