# Break It On Purpose

**Predictive Failure Detection for Self-Healing AWS Architectures**

> Learn what *normal* looks like, then catch a failure while it's still forming —
> seconds before a threshold alarm would ever fire — and let the system heal itself.

---

## The idea

Traditional cloud monitoring is **reactive**: an alarm trips *after* CPU crosses 90%,
*after* latency spikes, *after* users are already seeing errors. By then you're
recovering from an outage instead of preventing one.

**Break It On Purpose** flips this. We train an **Isolation Forest** on
high-resolution metrics collected during *normal* operation only. The model learns
the shape of "healthy," so any drift toward failure registers as an anomaly — often
**seconds before** a static threshold would react. That early warning drives a
**self-healing loop**: the detector publishes to SNS, a Lambda pre-emptively scales
out or restarts the service, and the system recovers *before* the failure becomes
user-facing.

The name is literal: we **inject faults on purpose** (chaos engineering) to generate
a labeled dataset of real failure transitions, then measure how much earlier the model
warns compared to conventional alarms.

## Why it's different

- **Predictive, not reactive.** Features are engineered for *trend* — rolling
  mean/std, rate-of-change (slope), short-horizon deltas — so the model reacts to
  *where a metric is heading*, not just where it is.
- **App-level signals, not just CPU.** Latency percentiles (p50/p95/p99), error rate,
  in-flight requests, and DB connection-pool utilisation are captured from the start.
- **Honest measurement.** A **three-arm comparison** separates model gains from
  the gains you'd get just by sampling faster (see below).
- **Reproducible.** The labeled failure-transition dataset and all code are published,
  so the results can be re-run — not just claimed.

## How we measure it (three-arm comparison)

To prove the model earns its keep, every failure run is scored three ways:

| Arm | Detector | Answers |
|-----|----------|---------|
| 1 | Standard CloudWatch alarm | The conventional baseline everyone ships with |
| 2 | High-resolution threshold | How much comes from *sampling faster* alone |
| 3 | **Isolation Forest (ours)** | How much the *model* adds on top of arm 2 |

**Headline metric:** lead time in seconds over the high-res baseline (arm 2), reported
as **mean ± std / CI** across many runs — never a single cherry-picked number.
We also report false-positive rate over a long quiet window and mean recovery time (RTO).

## Architecture

```
                    ┌──────────────┐     metrics      ┌──────────────┐
     load  ───────► │  Flask app   │ ───────────────► │ metric agent │
   (hey / AB)       │  (EC2 / ASG) │  system + app    │  (psutil)    │
                    └──────┬───────┘  signals          └──────┬───────┘
                           │                                   │ engineered
                    ┌──────▼───────┐                    ┌──────▼───────┐
                    │  ALB + ASG   │                    │  Isolation   │
                    │ (self-heal)  │ ◄──── Lambda ◄─SNS─│    Forest    │
                    └──────────────┘   scale / restart  │  (detector)  │
                                                        └──────────────┘
                            ▲                                   │
                            └────── chaos: fault injector ──────┘
                                  (CPU stress, instance kill, DB fault)
```

Everything left of the detector is built and validated **locally first**. AWS is
provisioned by scripts (one-command up/down) and torn down after every session, so
credits are spent only on real chaos runs.

## Repo layout

```
app/        Flask service with observability baked in     — Phase 1
agent/      psutil metric agent + trend feature pipeline   — Phase 2
model/      Isolation Forest training & offline validation — Phase 3
infra/      Scripted AWS up / down (boto3 or IaC)          — Phase 0/4
chaos/      Lambda fault injector + experiment drivers     — Phase 5
analysis/   Results, plots, ablation study                 — Phase 7
data/       Labeled failure-transition dataset             — Phase 5
```

## Tech stack

**App & metrics:** Python, Flask, psutil · **ML:** scikit-learn (Isolation Forest),
pandas, NumPy · **Load:** `hey` / Apache Bench · **Cloud:** EC2, RDS (Single-AZ),
Auto Scaling + ALB, Lambda, SNS, EventBridge, S3, CloudWatch · **Region:**
`ap-south-1` (Mumbai) · **Plots:** matplotlib + CloudWatch dashboards

## Getting started (local)

> The whole pipeline runs on a laptop — no AWS account needed until chaos experiments.

```bash
git clone https://github.com/KaChxKC/Break-It-On-Purpose.git
cd Break-It-On-Purpose

python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt   # added in Phase 1
```

Component-level run instructions land in each folder's README as the phases are built.

## Roadmap

- [x] **Phase 0** — Foundations, safety rails, repo setup
- [ ] **Phase 1** — Flask app with observability baked in *(local)*
- [ ] **Phase 2** — Metric agent + trend feature pipeline *(local)*
- [ ] **Phase 3** — Isolation Forest trained & validated *(local)*
- [ ] **Phase 4** — Stand up real AWS infra, scripted
- [ ] **Phase 5** — Chaos experiments & data collection
- [ ] **Phase 6** — Self-healing loop (SNS → Lambda → scale/restart)
- [ ] **Phase 7** — Analysis, three-arm comparison, ablation
- [ ] **Phase 8** — Write-up & presentation

## Team

| **Team** |
|---|
| **Shreya Shirsh**  |
| **Kartikey Chauhan**  |


