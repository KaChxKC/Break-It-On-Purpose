from agent.collector import Collector
from agent.schema import APP_COLUMNS, COLUMNS, SYSTEM_GAUGES


def test_sample_has_full_schema():
    # Point at a closed port so the app scrape fails fast; the system half of
    # the row must still be present.
    collector = Collector("http://127.0.0.1:9/metrics", instance="test", timeout=0.5)
    row = collector.sample()
    assert set(row.keys()) == set(COLUMNS)


def test_app_fields_none_when_unreachable():
    collector = Collector("http://127.0.0.1:9/metrics", instance="test", timeout=0.5)
    row = collector.sample()
    # app unreachable -> every app field is None
    assert all(row[col] is None for col in APP_COLUMNS)
    # system gauges are still captured
    assert all(row[col] is not None for col in SYSTEM_GAUGES)
    assert row["schema_version"] and row["instance"] == "test"
