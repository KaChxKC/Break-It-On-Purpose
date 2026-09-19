"""Turn raw metric samples into engineered trend features.

This is where "predictive" comes from: raw instantaneous values mostly just
reproduce a static threshold, whereas rolling statistics, slope and short-horizon
deltas describe *where a metric is heading*. Every feature is causal — computed
from the current sample and past samples only — so there is no lookahead leakage
into the model's view of a failure transition.
"""

import numpy as np
import pandas as pd

from .schema import COUNTER_COLUMNS

# Base signals fed into trend features. Counter rates are added by add_rates()
# before this list is used.
DEFAULT_SIGNALS = [
    "cpu_percent",
    "mem_percent",
    "app_latency_p95_ms",
    "app_latency_p99_ms",
    "app_in_flight",
    "app_pool_utilization",
    "app_requests_total_rate",
    "app_errors_total_rate",
    "disk_read_bytes_rate",
    "disk_write_bytes_rate",
    "net_sent_bytes_rate",
    "net_recv_bytes_rate",
]


def load_raw(path: str) -> pd.DataFrame:
    df = pd.read_json(path, lines=True)
    df = df.sort_values("ts_epoch").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts_epoch"], unit="s", utc=True)
    return df


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert cumulative counters into per-second rates via causal differencing."""
    dt = df["ts_epoch"].diff()
    for col in COUNTER_COLUMNS:
        if col in df.columns:
            df[f"{col}_rate"] = df[col].diff() / dt
    return df


def _slope(window: np.ndarray) -> float:
    """Least-squares slope over a window (units per sample)."""
    if np.isnan(window).any():
        return np.nan
    x = np.arange(len(window))
    return float(np.polyfit(x, window, 1)[0])


def add_trend_features(
    df: pd.DataFrame,
    signals: list[str],
    window: int = 5,
    delta_horizon: int = 3,
) -> pd.DataFrame:
    for sig in signals:
        if sig not in df.columns:
            continue
        s = df[sig]
        roll = s.rolling(window, min_periods=window)
        df[f"{sig}_mean"] = roll.mean()
        df[f"{sig}_std"] = roll.std()
        df[f"{sig}_slope"] = roll.apply(_slope, raw=True)
        df[f"{sig}_delta"] = s - s.shift(delta_horizon)
    return df


def build_features(
    path: str,
    window: int = 5,
    delta_horizon: int = 3,
    signals: list[str] | None = None,
    drop_warmup: bool = True,
) -> pd.DataFrame:
    df = load_raw(path)
    df = add_rates(df)
    signals = signals if signals is not None else DEFAULT_SIGNALS
    df = add_trend_features(df, signals, window, delta_horizon)

    if drop_warmup:
        # Trim only the initial rows whose rolling windows are not yet full.
        # We drop by position (not dropna) so that rows where the app is
        # unreachable — NaN app fields, but a real part of a failure — survive.
        warmup = max(window, delta_horizon + 1) - 1
        df = df.iloc[warmup:].reset_index(drop=True)
    return df
