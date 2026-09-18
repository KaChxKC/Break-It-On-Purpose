from time import perf_counter

from flask import g, request

# Administrative endpoints are excluded from workload metrics so that frequent
# ALB health checks and metric scrapes don't drown out the real request
# latency/throughput signal the model depends on.
EXCLUDED_PATHS = {"/health", "/metrics"}


def register_instrumentation(app, registry) -> None:
    @app.before_request
    def _before():
        if request.path in EXCLUDED_PATHS:
            return
        g.start = perf_counter()
        registry.inc_inflight()
        g.inflight = True

    @app.after_request
    def _after(response):
        g.status = response.status_code
        return response

    @app.teardown_request
    def _teardown(exc):
        # teardown always runs exactly once per request, so it is the reliable
        # place to balance the in-flight gauge and record the sample — even when
        # an unhandled exception skips after_request.
        if getattr(g, "inflight", False):
            registry.dec_inflight()
            g.inflight = False
        start = getattr(g, "start", None)
        if start is not None:
            duration_ms = (perf_counter() - start) * 1000.0
            status = getattr(g, "status", None)
            is_error = exc is not None or (status is not None and status >= 500)
            registry.record(duration_ms, is_error)
            g.start = None
