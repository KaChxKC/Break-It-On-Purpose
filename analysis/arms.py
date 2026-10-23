"""The three detection arms, so model gains are separable from sampling gains.

  arm 1  CloudWatch-style alarm : the raw metric, coarse-resampled (1-min), thresholded
  arm 2  high-resolution threshold: the same raw metric at native resolution, thresholded
  arm 3  Isolation Forest        : sustained anomaly on engineered trend features

Each returns the epoch it would first fire on a run. Comparing arm 3 to arm 2
isolates what the *model* adds beyond merely sampling faster (arm 2 vs arm 1).
"""

import pandas as pd

DEFAULT_METRIC = "cpu_percent"
DEFAULT_THRESHOLD = 70.0


def first_sustained(mask, k: int = 3) -> int | None:
    run = 0
    for i, val in enumerate(mask):
        run = run + 1 if val else 0
        if run >= k:
            return i - k + 1
    return None


def cloudwatch_alarm_epoch(raw: pd.DataFrame, metric=DEFAULT_METRIC,
                           threshold=DEFAULT_THRESHOLD, periods=1,
                           resample_s=60) -> float | None:
    """Arm 1: coarse 1-minute-mean series thresholded for `periods` periods."""
    series = (raw.assign(ts=pd.to_datetime(raw["ts_epoch"], unit="s", utc=True))
                 .set_index("ts")[metric]
                 .resample(f"{resample_s}s").mean())
    idx = first_sustained((series >= threshold).to_numpy(), periods)
    if idx is None:
        return None
    # The alarm can only evaluate once the breaching period(s) complete, so it
    # fires at the END of the last period — this is the coarse arm's inherent lag.
    return series.index[idx].timestamp() + periods * resample_s


def highres_threshold_epoch(raw: pd.DataFrame, metric=DEFAULT_METRIC,
                            threshold=DEFAULT_THRESHOLD, k=3) -> float | None:
    """Arm 2: same threshold on the native high-resolution series."""
    idx = first_sustained(raw[metric].to_numpy() >= threshold, k)
    return None if idx is None else float(raw["ts_epoch"].to_numpy()[idx])


def model_detection_epoch(detector, feats: pd.DataFrame, k=3) -> float | None:
    """Arm 3: first sustained Isolation Forest anomaly."""
    idx = first_sustained(detector.is_anomaly(feats), k)
    return None if idx is None else float(feats["ts_epoch"].to_numpy()[idx])


def three_arm_epochs(raw, feats, detector, metric=DEFAULT_METRIC,
                     threshold=DEFAULT_THRESHOLD) -> dict:
    return {
        "cloudwatch": cloudwatch_alarm_epoch(raw, metric, threshold),
        "highres": highres_threshold_epoch(raw, metric, threshold),
        "model": model_detection_epoch(detector, feats),
    }


def run_lead_times(raw, feats, detector, metric=DEFAULT_METRIC,
                   threshold=DEFAULT_THRESHOLD) -> dict:
    """Lead times (seconds earlier) for one run. Positive = earlier warning."""
    t = three_arm_epochs(raw, feats, detector, metric, threshold)

    def lead(later, earlier):
        if t[later] is None or t[earlier] is None:
            return None
        return t[later] - t[earlier]

    return {
        "epochs": t,
        "model_over_highres": lead("highres", "model"),      # the headline
        "highres_over_cloudwatch": lead("cloudwatch", "highres"),  # sampling gain
        "model_over_cloudwatch": lead("cloudwatch", "model"),
    }
