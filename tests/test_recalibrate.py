import os
import tempfile

from agent.features import build_features
from agent.synthetic import generate_run, write_jsonl
from heal.recalibrate import recalibrate
from model.model import Detector


def test_recalibrate_produces_working_model(tmp_path):
    normal_rows, _ = generate_run(n_normal=250, n_fault=0, seed=5)
    normal_path = tmp_path / "cloud_normal.jsonl"
    write_jsonl(normal_rows, str(normal_path))
    model_path = tmp_path / "detector.joblib"

    recalibrate(str(normal_path), str(model_path), contamination=0.02)
    assert model_path.exists()

    # the saved model detects a fault run
    det = Detector.load(str(model_path))
    fault_rows, onset = generate_run(n_normal=60, n_fault=60, seed=6)
    fpath = os.path.join(tempfile.gettempdir(), "recal_fault.jsonl")
    write_jsonl(fault_rows, fpath)
    try:
        feats = build_features(fpath)
    finally:
        os.remove(fpath)
    is_fault = (feats["ts_epoch"] >= onset).to_numpy()
    assert det.is_anomaly(feats)[is_fault].mean() > 0.7
