"""The metric schema: the single, versioned source of truth for what the agent
samples. Freeze this before collecting data — adding or renaming a column
mid-collection silently breaks the concatenation of runs.

Counter columns are cumulative and monotonic; the feature pipeline derives
rates from their deltas between samples. Gauge columns are instantaneous.
"""

SCHEMA_VERSION = "1.0.0"

# Metadata written on every row.
META_COLUMNS = ["schema_version", "instance", "ts_epoch", "ts_iso"]

# System signals sampled via psutil.
SYSTEM_GAUGES = [
    "cpu_percent",       # since previous sample
    "mem_percent",
    "mem_used_mb",
]
SYSTEM_COUNTERS = [
    "disk_read_bytes",   # cumulative
    "disk_write_bytes",
    "net_sent_bytes",
    "net_recv_bytes",
]

# Application signals scraped from the Flask app's /metrics endpoint.
APP_COUNTERS = [
    "app_requests_total",
    "app_errors_total",
]
APP_GAUGES = [
    "app_in_flight",
    "app_latency_p50_ms",
    "app_latency_p95_ms",
    "app_latency_p99_ms",
    "app_pool_utilization",
]

# Columns that accumulate and must be differenced to become a rate.
COUNTER_COLUMNS = SYSTEM_COUNTERS + APP_COUNTERS

# Full ordered column list for one sample.
COLUMNS = (
    META_COLUMNS
    + SYSTEM_GAUGES
    + SYSTEM_COUNTERS
    + APP_COUNTERS
    + APP_GAUGES
)

# App fields are None when the app is unreachable (e.g. during a DB-fault run);
# system fields are still captured so the failure transition stays observable.
APP_COLUMNS = APP_COUNTERS + APP_GAUGES
