import pytest

from app import create_app
from app.config import Config


@pytest.fixture
def client(monkeypatch):
    # No database is needed for these routes; skip the best-effort schema setup so
    # the test never waits on a connection attempt.
    monkeypatch.setattr("app.init_models", lambda engine: None)
    config = Config(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/none",
        db_pool_size=2,
        db_max_overflow=1,
        app_host="127.0.0.1",
        app_port=0,
        waitress_threads=2,
        latency_window=50,
        instance_id="test-instance",
    )
    return create_app(config).test_client()


def test_dashboard_page_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    assert b"Break It On Purpose" in page.data


def test_metrics_includes_system_block(client):
    body = client.get("/metrics").get_json()
    assert body["instance"] == "test-instance"
    assert set(body["system"]) == {"cpu_percent", "process_cpu_percent", "mem_percent"}
    assert 0 <= body["system"]["mem_percent"] <= 100
    # fields the metric agent already depends on are untouched
    for key in ("requests_total", "errors_total", "in_flight", "latency_ms", "pool"):
        assert key in body


def test_load_fail_pct_injects_errors(client):
    assert client.get("/load?fail_pct=100").status_code == 500
    assert client.get("/load?fail_pct=0").status_code == 200


def test_load_cpu_time_burn_runs(client):
    for query in ("/load?cpu_ms=20", "/load?cpu_ms=20&cpu_time=1"):
        body = client.get(query).get_json()
        assert client.get(query).status_code == 200
        assert body["elapsed_ms"] >= 15


def test_metrics_carries_server_time(client):
    assert client.get("/metrics").get_json()["server_time"] > 0


def test_reset_window_clears_latency_but_not_counters(client):
    for _ in range(4):
        client.get("/load?cpu_ms=10")
    before = client.get("/metrics").get_json()
    assert before["latency_ms"]["samples"] == 4

    reset = client.post("/metrics/reset-window")
    assert reset.status_code == 200
    assert reset.get_json()["instance"] == "test-instance"

    after = client.get("/metrics").get_json()
    assert after["latency_ms"]["samples"] == 0
    assert after["requests_total"] == before["requests_total"]   # counters untouched


def test_reset_window_is_not_counted_as_workload(client):
    client.post("/metrics/reset-window")
    assert client.get("/metrics").get_json()["requests_total"] == 0


def test_injected_failures_are_counted_as_errors(client):
    for _ in range(3):
        client.get("/load?fail_pct=100")
    body = client.get("/metrics").get_json()
    assert body["errors_total"] == 3
    assert body["requests_total"] == 3
