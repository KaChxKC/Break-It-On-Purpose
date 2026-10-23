import json

import pytest

from chaos.experiment import (
    ExperimentConfig,
    run_batch,
    run_experiment,
    validate_manifest,
)
from chaos.faults import FaultResult


def _fake_fault(aws, **kwargs):
    return FaultResult("fake", onset_epoch=1000.0, offset_epoch=1030.0,
                       detail={"kwargs": kwargs})


def _fast_config(tmp_path):
    return ExperimentConfig(steady_s=0, observe_s=0, out_dir=str(tmp_path))


def test_manifest_has_labels_and_file(tmp_path):
    manifest = run_experiment(None, _fake_fault, run_id="r1", config=_fast_config(tmp_path))
    assert manifest["fault"] == "fake"
    assert manifest["fault_onset_epoch"] == 1000.0
    assert manifest["run_ended_epoch"] >= manifest["run_started_epoch"]
    saved = json.loads((tmp_path / "r1.json").read_text())
    assert saved["run_id"] == "r1"


def test_validate_manifest_rejects_missing_label():
    with pytest.raises(ValueError):
        validate_manifest({"run_id": "x", "fault": "f"})  # missing onset/times


def test_load_driver_invoked_with_alb(tmp_path):
    calls = []
    run_experiment(None, _fake_fault, run_id="r2", alb_dns="alb.example.com",
                   config=ExperimentConfig(steady_s=0, observe_s=0, out_dir=str(tmp_path)),
                   load_driver=lambda dns, qps, dur: calls.append((dns, qps, dur)))
    assert calls and calls[0][0] == "alb.example.com"


def test_run_batch_distinct_ids(tmp_path):
    manifests = run_batch(None, _fake_fault, 3, config=_fast_config(tmp_path),
                          run_prefix="cpu")
    ids = [m["run_id"] for m in manifests]
    assert len(set(ids)) == 3
    assert all(i.startswith("cpu-") for i in ids)
