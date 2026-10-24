"""One chaos data-collection session against live infra.

  python -m chaos.run_session --alb <alb dns>   # after `python -m infra.manage up`

Baseline under steady load, then batches of each fault. Writes manifests to
data/runs/, per-run metric slices to data/runs/metrics/, full per-instance raw
files to data/raw/, and ships everything to S3. Load is generated in-process
(no `hey` needed); metrics are pulled off the instances over SSM.

Note: `db_sg_block` alone has no effect (security groups are stateful, so the
app's open pooled connections survive) — run `python -m chaos.db_block_hard`
for the DB-connectivity fault instead, and pass `--dbblock 0` here.
"""

import argparse
import base64
import gzip
import json
import os
import threading
import time
import urllib.request

from infra.aws import Aws
from infra.config import InfraConfig

from . import faults
from .experiment import ExperimentConfig, run_experiment
from .shipping import ensure_data_bucket, upload_run

RAW_DIR = "data/raw"
SLICE_DIR = "data/runs/metrics"
CHUNK = 20000  # stay under SSM's 24k stdout cap


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------- load ----------
class PyLoad:
    def __init__(self, alb, qps, duration_s):
        self.stop = threading.Event()
        self.url = f"http://{alb}/load?cpu_ms=20&queries=1"
        self.qps, self.end = qps, time.time() + duration_s
        self.ok = self.err = 0
        self.threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(16)]
        for t in self.threads:
            t.start()

    def _worker(self):
        interval = 16.0 / self.qps
        while not self.stop.is_set() and time.time() < self.end:
            t0 = time.time()
            try:
                urllib.request.urlopen(self.url, timeout=5).read()
                self.ok += 1
            except Exception:
                self.err += 1
            time.sleep(max(0.0, interval - (time.time() - t0)))

    def terminate(self):
        self.stop.set()


def py_load_driver(alb, qps, duration_s):
    return PyLoad(alb, qps, duration_s)


# ---------- metric collection over SSM ----------
def ssm_run(aws, iid, cmd, timeout=60):
    ssm = aws.client("ssm")
    cid = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                           Parameters={"commands": [cmd]})["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            if inv["Status"] != "Success":
                raise RuntimeError(f"{iid}: {inv['Status']} {inv['StandardErrorContent'][:300]}")
            return inv["StandardOutputContent"]
    raise TimeoutError(f"ssm on {iid} timed out")


def fetch_metrics(aws, iid):
    """Pull the instance's full metrics.jsonl (gzip+base64, chunked)."""
    src = "/opt/app/data/raw/metrics.jsonl"
    ssm_run(aws, iid, f"gzip -c {src} | base64 -w0 > /tmp/m.b64")
    size = int(ssm_run(aws, iid, "stat -c %s /tmp/m.b64").strip())
    parts = []
    for off in range(0, size, CHUNK):
        parts.append(ssm_run(aws, iid, f"tail -c +{off + 1} /tmp/m.b64 | head -c {CHUNK}"))
    data = gzip.decompress(base64.b64decode("".join(parts)))
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{iid}.jsonl")
    with open(path, "wb") as f:
        f.write(data)
    return path


def fetch_all(aws):
    paths = []
    for iid in faults.asg_instance_ids(aws):
        try:
            paths.append(fetch_metrics(aws, iid))
        except Exception as exc:
            log(f"  fetch {iid} failed: {exc}")
    return paths


def slice_run(manifest, pad_s=5):
    lo, hi = manifest["run_started_epoch"] - pad_s, manifest["run_ended_epoch"] + pad_s
    os.makedirs(SLICE_DIR, exist_ok=True)
    out = []
    for name in os.listdir(RAW_DIR):
        rows = []
        with open(os.path.join(RAW_DIR, name), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if lo <= r.get("ts_epoch", 0) <= hi:
                    rows.append(line)
        if rows:
            p = os.path.join(SLICE_DIR, f"{manifest['run_id']}__{name}")
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(rows)
            out.append(p)
    return out


def ship_factory(aws, bucket):
    def ship(manifest):
        paths = fetch_all(aws)
        slices = slice_run(manifest)
        upload_run(aws, bucket, manifest, slices)
        log(f"  {manifest['run_id']}: {len(slices)} metric slices from {len(paths)} instances")
    return ship


# ---------- health ----------
def wait_healthy(aws, want=2, timeout=900):
    elb = aws.client("elbv2")
    tg = elb.describe_target_groups(Names=[aws.config.name("tg")])["TargetGroups"][0]["TargetGroupArn"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = elb.describe_target_health(TargetGroupArn=tg)["TargetHealthDescriptions"]
        n = sum(1 for d in h if d["TargetHealth"]["State"] == "healthy")
        if n >= want:
            return n
        time.sleep(15)
    raise TimeoutError(f"targets not healthy ({want}) after {timeout}s")


def wait_db_available(aws, timeout=600):
    rds = aws.client("rds")
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = rds.describe_db_instances(DBInstanceIdentifier=aws.config.name("db"))["DBInstances"][0]
        if s["DBInstanceStatus"] == "available":
            return
        time.sleep(10)
    raise TimeoutError("db not available")


# ---------- session ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alb", required=True)
    ap.add_argument("--baseline-s", type=float, default=600)
    ap.add_argument("--cpu", type=int, default=5)
    ap.add_argument("--dbblock", type=int, default=0,
                    help="db_sg_block runs (no effect on open connections; see module doc)")
    ap.add_argument("--dbreboot", type=int, default=3)
    ap.add_argument("--kill", type=int, default=3)
    args = ap.parse_args()

    aws = Aws(InfraConfig())
    bucket = ensure_data_bucket(aws)
    cfg = ExperimentConfig()
    log(f"bucket={bucket}")
    wait_healthy(aws)
    log("targets healthy")

    ship = ship_factory(aws, bucket)

    # Quiet baseline under steady load: "normal" training data + FP window.
    if args.baseline_s > 0:
        log(f"baseline {args.baseline_s}s")
        started = time.time()
        load = py_load_driver(args.alb, cfg.load_qps, args.baseline_s)
        time.sleep(args.baseline_s)
        load.terminate()
        m = {"run_id": time.strftime("baseline-%Y%m%dT%H%M%SZ", time.gmtime()),
             "fault": "none", "fault_onset_epoch": None,
             "run_started_epoch": started, "run_ended_epoch": time.time(),
             "alb_dns": args.alb, "load_ok": load.ok, "load_err": load.err}
        os.makedirs("data/runs", exist_ok=True)
        with open(f"data/runs/{m['run_id']}.json", "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
        ship(m)

    def batch(name, n, fault_fn, after=None):
        for i in range(n):
            rid = f"{name}-{i:03d}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            log(f"run {rid}")
            try:
                m = run_experiment(aws, fault_fn, alb_dns=args.alb, config=cfg,
                                   run_id=rid, load_driver=py_load_driver)
                ship(m)
            except Exception as exc:
                log(f"  run {rid} FAILED: {exc}")
            if after:
                after()

    def cpu_fault(a):
        return faults.cpu_stress(a, faults.asg_instance_ids(a), duration_s=60, workers=2)

    def db_block_fault(a):
        return faults.db_sg_block(a, duration_s=45)

    def kill_fault(a):
        victim = faults.asg_instance_ids(a)[0]
        try:
            fetch_metrics(a, victim)  # save its data before it dies
        except Exception as exc:
            log(f"  pre-kill fetch failed: {exc}")
        return faults.kill_instance(a, victim)

    batch("cpu_stress", args.cpu, cpu_fault)
    batch("db_sg_block", args.dbblock, db_block_fault)
    batch("db_reboot", args.dbreboot, faults.db_reboot,
          after=lambda: (wait_db_available(aws), time.sleep(20)))
    batch("instance_kill", args.kill, kill_fault, after=lambda: wait_healthy(aws))
    log("SESSION DONE")


if __name__ == "__main__":
    main()
