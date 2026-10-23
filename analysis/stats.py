import numpy as np

from .arms import first_sustained, highres_threshold_epoch, model_detection_epoch


def bootstrap_ci(values, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> dict:
    """Distribution-free mean + CI, so a small n doesn't rest on a normality
    assumption. Reports n explicitly."""
    vals = np.asarray([v for v in values if v is not None], dtype=float)
    vals = vals[~np.isnan(vals)]
    if vals.size == 0:
        return {"n": 0, "mean": None, "std": None, "ci_low": None, "ci_high": None}
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(vals, vals.size, replace=True).mean()
                      for _ in range(n_boot)])
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "n": int(vals.size),
        "mean": float(vals.mean()),
        "std": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
        "ci_low": float(lo),
        "ci_high": float(hi),
    }


def threshold_fires(raw, metric, threshold, k=3) -> bool:
    return first_sustained(raw[metric].to_numpy() >= threshold, k) is not None


def model_false_positive_rate(detector, feats) -> float:
    return float(np.mean(detector.is_anomaly(feats)))


def rto(raw, onset_epoch, metric, threshold, recover_k: int = 5) -> float | None:
    """Recovery time: seconds from fault onset until the metric returns below
    threshold for `recover_k` consecutive samples."""
    ts = raw["ts_epoch"].to_numpy()
    vals = raw[metric].to_numpy()
    after = ts >= onset_epoch
    sub_ts, sub_vals = ts[after], vals[after]
    # Only count recovery once the fault has actually manifested (metric breached
    # the threshold); otherwise the quiet moment right after onset reads as 0.
    breach = first_sustained(sub_vals >= threshold, 1)
    if breach is None:
        return None
    idx = first_sustained(sub_vals[breach:] < threshold, recover_k)
    return None if idx is None else float(sub_ts[breach + idx] - onset_epoch)


def ablation(raw, feats, detector, metric_thresholds: dict, k: int = 3) -> dict:
    """Which single metric warns earliest, and does the model beat it?"""
    per_metric = {m: highres_threshold_epoch(raw, m, thr, k)
                  for m, thr in metric_thresholds.items() if m in raw.columns}
    model_epoch = model_detection_epoch(detector, feats, k)
    valid = {m: t for m, t in per_metric.items() if t is not None}
    earliest = min(valid, key=valid.get) if valid else None
    beats = bool(earliest is not None and model_epoch is not None
                 and model_epoch < valid[earliest])
    return {
        "per_metric_epoch": per_metric,
        "earliest_metric": earliest,
        "earliest_epoch": valid.get(earliest) if earliest else None,
        "model_epoch": model_epoch,
        "model_beats_best_single": beats,
    }
