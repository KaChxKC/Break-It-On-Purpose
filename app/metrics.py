import threading
from collections import deque


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile over an already-sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


class MetricsRegistry:
    """Thread-safe application metrics.

    Latency percentiles are computed over a bounded sliding window (the last
    `window` requests) so they reflect current behaviour rather than all-time
    history. Request/error totals are exposed as monotonic counters so the
    metric agent can derive throughput and error rate from deltas between
    samples, the way a Prometheus counter is scraped.
    """

    def __init__(self, window: int) -> None:
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=window)
        self._requests_total = 0
        self._errors_total = 0
        self._in_flight = 0

    def inc_inflight(self) -> None:
        with self._lock:
            self._in_flight += 1

    def dec_inflight(self) -> None:
        with self._lock:
            self._in_flight -= 1

    def record(self, duration_ms: float, is_error: bool) -> None:
        with self._lock:
            self._latencies.append(duration_ms)
            self._requests_total += 1
            if is_error:
                self._errors_total += 1

    def snapshot(self) -> dict:
        with self._lock:
            latencies = sorted(self._latencies)
            requests_total = self._requests_total
            errors_total = self._errors_total
            in_flight = self._in_flight
        n = len(latencies)
        return {
            "requests_total": requests_total,
            "errors_total": errors_total,
            "error_rate": round(errors_total / requests_total, 4) if requests_total else 0.0,
            "in_flight": in_flight,
            "latency_ms": {
                "p50": round(_percentile(latencies, 50), 2),
                "p95": round(_percentile(latencies, 95), 2),
                "p99": round(_percentile(latencies, 99), 2),
                "max": round(latencies[-1], 2) if n else 0.0,
                "samples": n,
            },
        }
