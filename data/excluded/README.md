# Excluded runs

`db_sg_block-*` (5 runs, 2026-10-24): **the fault had no effect.** Security groups
are stateful, so revoking the RDS ingress rule did not break the app's
already-open pooled connections — no errors, no latency change. Labels would
claim a failure that never happened, so these are kept out of `data/runs/`.

Superseded by `db_block_hard-*` runs, which also `pg_terminate_backend` the
existing connections right after the revoke (onset = terminate time).
