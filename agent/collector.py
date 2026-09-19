import json
import socket
import time
import urllib.request
from datetime import datetime, timezone

import psutil

from .schema import APP_COLUMNS, SCHEMA_VERSION


class Collector:
    """Samples one row of the metric schema per call: system signals from
    psutil plus the app's own signals scraped from its /metrics endpoint.
    """

    def __init__(self, app_url: str, instance: str | None = None, timeout: float = 2.0) -> None:
        self.app_url = app_url
        self.instance = instance or socket.gethostname()
        self.timeout = timeout
        # Prime cpu_percent: the first interval=None call always returns 0.0, so
        # discard it here and let each real sample report usage since the prior tick.
        psutil.cpu_percent(interval=None)

    def _sample_system(self) -> dict:
        vm = psutil.virtual_memory()
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "mem_percent": vm.percent,
            "mem_used_mb": round(vm.used / (1024 * 1024), 2),
            "disk_read_bytes": disk.read_bytes if disk else None,
            "disk_write_bytes": disk.write_bytes if disk else None,
            "net_sent_bytes": net.bytes_sent if net else None,
            "net_recv_bytes": net.bytes_recv if net else None,
        }

    def _scrape_app(self) -> dict:
        fields = {col: None for col in APP_COLUMNS}
        try:
            with urllib.request.urlopen(self.app_url, timeout=self.timeout) as resp:
                m = json.load(resp)
            latency = m["latency_ms"]
            fields.update(
                app_requests_total=m["requests_total"],
                app_errors_total=m["errors_total"],
                app_in_flight=m["in_flight"],
                app_latency_p50_ms=latency["p50"],
                app_latency_p95_ms=latency["p95"],
                app_latency_p99_ms=latency["p99"],
                app_pool_utilization=m["pool"]["utilization"],
            )
        except Exception:
            pass  # app unreachable — leave app fields None, keep the system row
        return fields

    def sample(self) -> dict:
        ts_epoch = time.time()
        row = {
            "schema_version": SCHEMA_VERSION,
            "instance": self.instance,
            "ts_epoch": ts_epoch,
            "ts_iso": datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat(),
        }
        row.update(self._sample_system())
        row.update(self._scrape_app())
        return row


def run(
    collector: Collector,
    out_path: str,
    interval_sec: float,
    max_samples: int | None = None,
    duration_sec: float | None = None,
) -> int:
    """Append one JSONL row per tick on a fixed cadence.

    Next-tick times are anchored to a monotonic start so a slow sample doesn't
    let the sampling interval drift — consistent spacing is what the feature
    pipeline's rolling windows and rate calculations rely on.
    """
    start = time.monotonic()
    n = 0
    with open(out_path, "a", encoding="utf-8") as f:
        while True:
            f.write(json.dumps(collector.sample()) + "\n")
            f.flush()
            n += 1
            if max_samples is not None and n >= max_samples:
                break
            if duration_sec is not None and (time.monotonic() - start) >= duration_sec:
                break
            sleep_for = (start + n * interval_sec) - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    return n
