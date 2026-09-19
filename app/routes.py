import time

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import text

from .models import db_check

main_bp = Blueprint("main", __name__)


def _busy_cpu(ms: float) -> None:
    # A pure-Python busy loop holds the GIL, so this deliberately consumes a
    # CPU core for the requested duration — the point is to generate real load.
    deadline = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < deadline:
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
      queries        - number of DB round-trips to issue
      query_sleep_ms - if > 0, each query is SELECT pg_sleep(...), holding a
                       pooled connection (used to pressure the pool in chaos runs)
    """
    cpu_ms = request.args.get("cpu_ms", default=0.0, type=float)
    queries = request.args.get("queries", default=0, type=int)
    query_sleep_ms = request.args.get("query_sleep_ms", default=0.0, type=float)
    db = current_app.config["DB"]

    start = time.perf_counter()

    if cpu_ms > 0:
        _busy_cpu(cpu_ms)

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
    snapshot["instance"] = current_app.config["APP_CONFIG"].instance_id
    return jsonify(snapshot)


@main_bp.get("/db/check")
def db_check_route():
    try:
        result = db_check(current_app.config["DB"].engine)
        return jsonify(ok=True, **result)
    except Exception as exc:
        return jsonify(ok=False, error=type(exc).__name__), 503
