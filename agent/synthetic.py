"""Generate a synthetic normal->failure metric run with a known fault onset.

Used to test the feature pipeline and, in Phase 3, to sanity-check offline that
the model warns *before* a naive threshold breach. Leading indicators (CPU,
latency, pool) start trending at the fault onset; the error rate spikes later —
so an early warning is possible only by reading the trend, not the raw value.
"""

from datetime import datetime, timezone

import numpy as np

from .schema import SCHEMA_VERSION


def generate_run(
    n_normal: int = 60,
    n_fault: int = 40,
    interval: float = 1.0,
    seed: int = 0,
    start_epoch: float = 1_000_000_000.0,
    instance: str = "synthetic",
) -> tuple[list[dict], float]:
    """Return (rows, fault_onset_epoch)."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    disk_r = disk_w = net_s = net_r = 0
    requests_total = errors_total = 0
    fault_onset = start_epoch + n_normal * interval

    for i in range(n_normal + n_fault):
        ts = start_epoch + i * interval
        in_fault = i >= n_normal
        prog = (i - n_normal) / n_fault if in_fault else 0.0  # 0..1 through the fault

        cpu = float(np.clip(20 + rng.normal(0, 2) + (60 * prog), 0, 100))
        p95 = float(20 + rng.normal(0, 1) + 150 * prog)
        # per-interval increments
        requests_total += 50
        err_rate = 0.001 + (0.3 * prog if in_fault else 0.0)
        errors_total += int(round(50 * err_rate))
        disk_r += 100_000 + int(rng.integers(0, 5_000))
        disk_w += 50_000 + int(rng.integers(0, 3_000))
        net_s += 200_000
        net_r += 300_000

        rows.append({
            "schema_version": SCHEMA_VERSION,
            "instance": instance,
            "ts_epoch": ts,
            "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "cpu_percent": round(cpu, 2),
            "mem_percent": round(float(50 + rng.normal(0, 1) + 20 * prog), 2),
            "mem_used_mb": round(float(1000 + 400 * prog), 2),
            "disk_read_bytes": disk_r,
            "disk_write_bytes": disk_w,
            "net_sent_bytes": net_s,
            "net_recv_bytes": net_r,
            "app_requests_total": requests_total,
            "app_errors_total": errors_total,
            "app_in_flight": int(round(2 + 20 * prog)),
            "app_latency_p50_ms": round(p95 * 0.5, 2),
            "app_latency_p95_ms": round(p95, 2),
            "app_latency_p99_ms": round(p95 * 1.4, 2),
            "app_pool_utilization": round(float(min(1.0, 0.1 + 0.8 * prog)), 3),
        })

    return rows, fault_onset


def write_jsonl(rows: list[dict], path: str) -> None:
    import json

    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
