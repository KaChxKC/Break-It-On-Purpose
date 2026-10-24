"""Supplemental batch: a DB-block fault that actually breaks existing connections.

Security groups are stateful, so revoking the RDS ingress rule alone leaves the
app's pooled connections alive. Here an instance opens a DB connection *before*
the revoke, then (after the revoke) uses it to pg_terminate_backend every other
app connection, so the pool must reconnect and hits the block.

  python -m chaos.db_block_hard --alb <alb dns> [--runs 5]

The manifest's fault_onset_epoch is the terminate time (when connectivity is
actually lost); the revoke time is kept in fault_detail.
"""

import argparse
import time

from infra.aws import Aws
from infra.config import InfraConfig

from . import faults
from .experiment import ExperimentConfig, run_experiment
from .run_session import log, py_load_driver, ship_factory
from .shipping import ensure_data_bucket

KILLER = r"""cd /opt/app && /opt/app/.venv/bin/python - <<'PY'
import time, psycopg
url = [l.split('=',1)[1].strip() for l in open('.env') if l.startswith('DATABASE_URL=')][0]
c = psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://'), autocommit=True)
time.sleep(__DELAY__)
t = time.time()
n = c.execute("select count(pg_terminate_backend(pid)) from pg_stat_activity "
              "where usename = current_user and pid <> pg_backend_pid()").fetchone()[0]
print(f"{t} {n}")
PY"""


def db_block_hard(aws, duration_s=45, delay_s=8):
    ssm = aws.client("ssm")
    iid = faults.asg_instance_ids(aws)[0]
    cid = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                           Parameters={"commands": [KILLER.replace("__DELAY__", str(delay_s))]}
                           )["Command"]["CommandId"]
    time.sleep(4)  # let the killer connect before the rule goes away
    res = faults.db_sg_block(aws, duration_s=duration_s)  # revoke, sleep, restore

    term_epoch, killed = None, None
    for _ in range(20):
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            out = inv["StandardOutputContent"].split()
            if inv["Status"] == "Success" and len(out) >= 2:
                term_epoch, killed = float(out[0]), int(out[1])
            break
        time.sleep(1)

    res.fault = "db_block_hard"
    res.detail.update({"revoke_epoch": res.onset_epoch, "terminate_epoch": term_epoch,
                       "backends_terminated": killed, "killer_instance": iid})
    # Connectivity is effectively lost when existing connections are killed.
    if term_epoch and res.onset_epoch < term_epoch < res.offset_epoch:
        res.onset_epoch = term_epoch
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alb", required=True)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    aws = Aws(InfraConfig())
    bucket = ensure_data_bucket(aws)
    ship = ship_factory(aws, bucket)
    cfg = ExperimentConfig()
    for i in range(args.runs):
        rid = f"db_block_hard-{i:03d}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        log(f"run {rid}")
        try:
            m = run_experiment(aws, db_block_hard, alb_dns=args.alb, config=cfg,
                               run_id=rid, load_driver=py_load_driver)
            log(f"  detail {m['fault_detail']}")
            ship(m)
        except Exception as exc:
            log(f"  run {rid} FAILED: {exc}")
        time.sleep(20)  # let the pool settle between runs
    log("HARD BATCH DONE")


if __name__ == "__main__":
    main()
