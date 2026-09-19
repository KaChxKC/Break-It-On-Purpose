import json

import numpy as np

from agent.features import build_features
from agent.synthetic import generate_run, write_jsonl


def _write_rows(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_rowcount_and_feature_columns(tmp_path):
    rows, _ = generate_run(n_normal=40, n_fault=20, seed=1)
    path = tmp_path / "run.jsonl"
    write_jsonl(rows, str(path))

    window, delta = 5, 3
    feats = build_features(str(path), window=window, delta_horizon=delta)

    warmup = max(window, delta + 1) - 1
    assert len(feats) == len(rows) - warmup
    for suffix in ("_mean", "_std", "_slope", "_delta"):
        assert any(c.endswith(suffix) for c in feats.columns)
    assert "cpu_percent_slope" in feats.columns


def test_slope_matches_known_ramp(tmp_path):
    # Deterministic linear ramp: cpu rises by exactly 3 per sample.
    rows, _ = generate_run(n_normal=20, n_fault=0, seed=0)
    for i, row in enumerate(rows):
        row["cpu_percent"] = 10.0 + 3.0 * i
    path = tmp_path / "ramp.jsonl"
    _write_rows(rows, str(path))

    feats = build_features(str(path), window=5, delta_horizon=3)
    assert np.allclose(feats["cpu_percent_slope"].dropna(), 3.0, atol=1e-9)


def test_counter_becomes_rate(tmp_path):
    rows, _ = generate_run(n_normal=30, n_fault=0, seed=2)
    # requests increment by 50 per 1s sample -> 50/s
    path = tmp_path / "rate.jsonl"
    write_jsonl(rows, str(path))
    feats = build_features(str(path), window=5, delta_horizon=3)
    assert np.allclose(feats["app_requests_total_rate"].dropna(), 50.0, atol=1e-6)


def test_features_are_causal(tmp_path):
    # A causal feature computed over the full series must equal the same feature
    # computed over only the data up to that point — i.e. no future leakage.
    rows, _ = generate_run(n_normal=40, n_fault=20, seed=3)
    full = tmp_path / "full.jsonl"
    write_jsonl(rows, str(full))

    k = 25
    trunc = tmp_path / "trunc.jsonl"
    write_jsonl(rows[:k], str(trunc))

    f_full = build_features(str(full), window=5, delta_horizon=3, drop_warmup=False)
    f_trunc = build_features(str(trunc), window=5, delta_horizon=3, drop_warmup=False)

    cols = [c for c in f_full.columns
            if c.endswith(("_mean", "_std", "_slope", "_delta", "_rate"))]
    a = f_full.loc[k - 1, cols].to_numpy(dtype=float)
    b = f_trunc.loc[k - 1, cols].to_numpy(dtype=float)
    assert np.allclose(a, b, atol=1e-9, equal_nan=True)


def test_app_unreachable_rows_survive(tmp_path):
    rows, _ = generate_run(n_normal=40, n_fault=20, seed=4)
    app_fields = ["app_requests_total", "app_errors_total", "app_in_flight",
                  "app_latency_p50_ms", "app_latency_p95_ms", "app_latency_p99_ms",
                  "app_pool_utilization"]
    for row in rows[45:50]:          # simulate app unreachable for 5 samples
        for fld in app_fields:
            row[fld] = None
    path = tmp_path / "gap.jsonl"
    _write_rows(rows, str(path))

    feats = build_features(str(path), window=5, delta_horizon=3)
    gap = feats[(feats["ts_epoch"] >= rows[45]["ts_epoch"])
                & (feats["ts_epoch"] <= rows[49]["ts_epoch"])]
    assert len(gap) == 5
    assert gap["app_latency_p95_ms"].isna().all()
