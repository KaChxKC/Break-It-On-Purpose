"""Gradual-degradation chaos: faults that build up over time, so a predictive
detector can warn *before* the user-facing SLA breaks (unlike the abrupt step
faults, where a raw threshold is already as fast as anything).

  python -m chaos.gradual --alb <alb dns>   # after `python -m infra.manage up`

  ramp_cpu  : load ramps up -> CPU and latency climb gradually
  pool_fill : slow-query load ramps -> the DB pool fills, in-flight+latency climb
  mem_leak  : an SSM script allocates memory in steps -> mem_percent climbs

Pair with `python -m analysis.sla`, which measures lead time to the SLA breach.
"""

import argparse
import json
import os
import threading
import time
import urllib.request

from infra.aws import Aws
from infra.config import InfraConfig

from . import faults
from .run_session import log, py_load_driver, ship_factory, wait_healthy
from .shipping import ensure_data_bucket


class RampLoad:
    """Load generator whose offered rate ramps linearly from q0 to q1 qps."""

    def __init__(self, alb, path, q0, q1, duration_s, threads=32):
        self.url = f"http://{alb}{path}"
        self.q0, self.q1, self.dur = q0, q1, float(duration_s)
        self.start = time.time()
        self.end = self.start + duration_s
        self.stop = threading.Event()
        self.ok = self.err = 0
        self.n = threads
        self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(threads)]
        for t in self._threads:
            t.start()

    def qps_now(self):
        frac = min(1.0, (time.time() - self.start) / self.dur)
        return self.q0 + (self.q1 - self.q0) * frac

    def _worker(self):
        while not self.stop.is_set() and time.time() < self.end:
            t0 = time.time()
            try:
                urllib.request.urlopen(self.url, timeout=10).read()
                self.ok += 1
            except Exception:
                self.err += 1
            interval = self.n / max(1.0, self.qps_now())
            time.sleep(max(0.0, interval - (time.time() - t0)))

    def terminate(self):
        self.stop.set()


# Allocates memory in steps and touches the pages so RSS actually grows.
MEMLEAK = r"""nohup /opt/app/.venv/bin/python - <<'PY' >/tmp/leak.log 2>&1 &
import time
chunks = []
for _ in range(__STEPS__):
    b = bytearray(__MB__ * 1024 * 1024)
    b[::4096] = bytes(len(b[::4096]))   # touch every page
    chunks.append(b)
    time.sleep(__INT__)
time.sleep(45)
PY"""


def _write_manifest(manifest):
    os.makedirs("data/runs", exist_ok=True)
    with open(f"data/runs/{manifest['run_id']}.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def ramp_run(aws, alb, name, path, q0, q1, duration_s, idx, ship):
    rid = f"{name}-{idx:03d}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    log(f"run {rid} (ramp {q0}->{q1} qps on {path})")
    started = time.time()
    ramp = RampLoad(alb, path, q0, q1, duration_s)
    time.sleep(duration_s)
    ramp.terminate()
    time.sleep(4)
    manifest = {
        "run_id": rid, "fault": name,
        "fault_onset_epoch": started,          # degradation begins as the ramp starts
        "fault_detail": {"ramp_qps": [q0, q1], "path": path,
                         "load_ok": ramp.ok, "load_err": ramp.err},
        "run_started_epoch": started, "run_ended_epoch": time.time(), "alb_dns": alb,
    }
    _write_manifest(manifest)
    ship(manifest)
    return manifest


def memleak_run(aws, alb, duration_s, mb, interval_s, idx, ship, qps=40):
    rid = f"mem_leak-{idx:03d}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    log(f"run {rid}")
    started = time.time()
    load = py_load_driver(alb, qps, duration_s)
    steps = max(1, int(duration_s / interval_s))
    script = (MEMLEAK.replace("__STEPS__", str(steps))
                     .replace("__MB__", str(mb))
                     .replace("__INT__", str(interval_s)))
    for iid in faults.asg_instance_ids(aws):
        aws.client("ssm").send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                                       Parameters={"commands": [script]})
    time.sleep(duration_s)
    load.terminate()
    time.sleep(4)
    manifest = {
        "run_id": rid, "fault": "mem_leak", "fault_onset_epoch": started,
        "fault_detail": {"mb_per_step": mb, "interval_s": interval_s, "steps": steps},
        "run_started_epoch": started, "run_ended_epoch": time.time(), "alb_dns": alb,
    }
    _write_manifest(manifest)
    ship(manifest)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alb", required=True)
    ap.add_argument("--ramp-cpu", type=int, default=8)
    ap.add_argument("--pool-fill", type=int, default=8)
    ap.add_argument("--mem-leak", type=int, default=5)
    ap.add_argument("--duration", type=float, default=120)
    args = ap.parse_args()

    aws = Aws(InfraConfig())
    bucket = ensure_data_bucket(aws)
    ship = ship_factory(aws, bucket)
    wait_healthy(aws)
    log("targets healthy; gradual session")

    for i in range(args.ramp_cpu):
        ramp_run(aws, args.alb, "ramp_cpu", "/load?cpu_ms=45&queries=1", 10, 250, args.duration, i, ship)
        time.sleep(15)
    for i in range(args.pool_fill):
        ramp_run(aws, args.alb, "pool_fill", "/load?query_sleep_ms=300&queries=1", 5, 120, args.duration, i, ship)
        time.sleep(20)
    for i in range(args.mem_leak):
        memleak_run(aws, args.alb, args.duration, mb=150, interval_s=8, idx=i, ship=ship)
        time.sleep(30)
    log("GRADUAL SESSION DONE")


if __name__ == "__main__":
    main()
