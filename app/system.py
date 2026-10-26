import threading
import time

import psutil

# CPU readings are "usage since the previous call", so several callers hitting
# /metrics close together would each see a tiny, noisy window. Sampling at most
# once per interval and serving the cached value keeps the number stable no
# matter how many clients poll.
_MIN_INTERVAL_S = 1.0

_lock = threading.Lock()
_process = psutil.Process()
_cache: dict = {"at": 0.0, "value": None}

# The first cpu_percent(interval=None) call always reports 0.0, so prime both
# counters at import and let the first real sample cover the time since startup.
psutil.cpu_percent(interval=None)
_process.cpu_percent(interval=None)


def snapshot() -> dict:
    now = time.monotonic()
    with _lock:
        if _cache["value"] is None or now - _cache["at"] >= _MIN_INTERVAL_S:
            _cache["value"] = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                # Share of ONE core used by this process (100 = a core fully busy),
                # which makes saturation of a single-process app visible on any host.
                "process_cpu_percent": _process.cpu_percent(interval=None),
                "mem_percent": psutil.virtual_memory().percent,
            }
            _cache["at"] = now
        return dict(_cache["value"])
