from agent.features import build_features, load_raw
from agent.synthetic import generate_run, write_jsonl
from analysis.sla import analyze_gradual, model_lead_to_sla, sla_breach_epoch
from model.model import Detector


def _raw_feats(rows, tmp_path, name):
    path = tmp_path / name
    write_jsonl(rows, str(path))
    return load_raw(str(path)), build_features(str(path))


def _detector(tmp_path):
    rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
    _, feats = _raw_feats(rows, tmp_path, "normal.jsonl")
    return Detector(contamination=0.02).fit(feats)


def test_no_breach_on_normal(tmp_path):
    rows, _ = generate_run(n_normal=200, n_fault=0, seed=3)
    raw, _ = _raw_feats(rows, tmp_path, "n.jsonl")
    # synthetic normal latency ~20ms, never near 100ms
    assert sla_breach_epoch(raw, latency_ms=100) is None


def test_model_warns_before_sla_on_gradual(tmp_path):
    det = _detector(tmp_path)
    rows, _ = generate_run(n_normal=100, n_fault=120, seed=4)  # latency ramps 20->170
    raw, feats = _raw_feats(rows, tmp_path, "f.jsonl")

    breach = sla_breach_epoch(raw, latency_ms=100)
    assert breach is not None                      # the ramp does breach 100ms
    lead = model_lead_to_sla(det, raw, feats, k=2, latency_ms=100)
    assert lead is not None and lead > 0           # model flags BEFORE the breach


def test_analyze_gradual_aggregates(tmp_path):
    det = _detector(tmp_path)
    runs = []
    for seed in (5, 6, 7):
        rows, _ = generate_run(n_normal=100, n_fault=120, seed=seed)
        raw, feats = _raw_feats(rows, tmp_path, f"r{seed}.jsonl")
        runs.append({"raw": raw, "feats": feats, "fault": "ramp_cpu"})
    summary = analyze_gradual(det, runs, k=2, latency_ms=100)
    assert summary["overall"]["mean"] > 0
    assert summary["ramp_cpu"]["n"] == 3
