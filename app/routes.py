import random
import time

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import text

from .models import db_check
from .system import snapshot as system_snapshot

main_bp = Blueprint("main", __name__)


def _busy_cpu(ms: float, cpu_time: bool = False) -> None:
    # A pure-Python busy loop holds the GIL, so this deliberately consumes a
    # CPU core for the requested duration — the point is to generate real load.
    # By default the duration is wall-clock time, so concurrent requests burn in
    # parallel. With cpu_time=True it is CPU time actually consumed by this
    # thread, so concurrent requests compete for the core and latency climbs as
    # the process saturates.
    clock = time.thread_time if cpu_time else time.perf_counter
    deadline = clock() + ms / 1000.0
    while clock() < deadline:
        pass


@main_bp.get("/")
def index():
    cfg = current_app.config["APP_CONFIG"]
    return render_template("status.html", instance=cfg.instance_id)


@main_bp.get("/load")
def load():
    """Synthetic workload: burn CPU and/or issue DB round-trips.

    Query params:
      cpu_ms         - busy-loop this many milliseconds (CPU burn)
      cpu_time       - 1 to count cpu_ms as CPU time consumed rather than wall time
      queries        - number of DB round-trips to issue
      query_sleep_ms - if > 0, each query is SELECT pg_sleep(...), holding a
                       pooled connection (used to pressure the pool in chaos runs)
      fail_pct       - percentage (0-100) of requests that fail with HTTP 500,
                       to exercise the error-rate signal
    """
    cpu_ms = request.args.get("cpu_ms", default=0.0, type=float)
    queries = request.args.get("queries", default=0, type=int)
    query_sleep_ms = request.args.get("query_sleep_ms", default=0.0, type=float)
    fail_pct = request.args.get("fail_pct", default=0.0, type=float)
    cpu_time = request.args.get("cpu_time", default=0, type=int) == 1
    db = current_app.config["DB"]

    if fail_pct > 0 and random.random() * 100 < fail_pct:
        return jsonify(error="injected failure"), 500

    start = time.perf_counter()

    if cpu_ms > 0:
        _busy_cpu(cpu_ms, cpu_time)

    executed = 0
    if queries > 0:
        if query_sleep_ms > 0:
            stmt, params = text("SELECT pg_sleep(:s)"), {"s": query_sleep_ms / 1000.0}
        else:
            stmt, params = text("SELECT 1"), {}
        with db.engine.connect() as conn:
            for _ in range(queries):
                conn.execute(stmt, params)
                executed += 1

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return jsonify(
        instance=current_app.config["APP_CONFIG"].instance_id,
        cpu_ms=cpu_ms,
        queries=executed,
        elapsed_ms=round(elapsed_ms, 2),
    )


@main_bp.get("/metrics")
def metrics():
    snapshot = current_app.config["METRICS"].snapshot()
    # pool_stats reads in-process counters, so /metrics keeps working even while
    # the database itself is unreachable — exactly when we most want the signal.
    snapshot["pool"] = current_app.config["DB"].pool_stats()
    snapshot["system"] = system_snapshot()
    snapshot["server_time"] = time.time()  # lets clients compute rates without network jitter
    snapshot["instance"] = current_app.config["APP_CONFIG"].instance_id
    return jsonify(snapshot)


@main_bp.post("/metrics/reset-window")
def reset_latency_window():
    """Clear the latency window so the next readings reflect only new traffic."""
    current_app.config["METRICS"].reset_latency()
    return jsonify(ok=True, instance=current_app.config["APP_CONFIG"].instance_id)


@main_bp.get("/db/check")
def db_check_route():
    try:
        result = db_check(current_app.config["DB"].engine)
        return jsonify(ok=True, **result)
    except Exception as exc:
        return jsonify(ok=False, error=type(exc).__name__), 503
