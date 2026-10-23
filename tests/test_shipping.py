import pytest
from moto import mock_aws

from chaos.shipping import data_bucket_name, ensure_data_bucket, list_runs, upload_run
from infra.aws import Aws
from infra.config import InfraConfig

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    with mock_aws():
        yield Aws(CFG)


def test_ensure_bucket_idempotent(aws_env):
    name = ensure_data_bucket(aws_env)
    assert name == data_bucket_name(aws_env)
    assert ensure_data_bucket(aws_env) == name  # no error on re-create
    buckets = [b["Name"] for b in aws_env.client("s3").list_buckets()["Buckets"]]
    assert name in buckets


def test_upload_and_list_run(aws_env, tmp_path):
    bucket = ensure_data_bucket(aws_env)
    metric_file = tmp_path / "metrics.jsonl"
    metric_file.write_text('{"ts_epoch": 1}\n')
    manifest = {"run_id": "run-001", "fault": "cpu_stress"}

    uri = upload_run(aws_env, bucket, manifest, metric_files=[str(metric_file)])
    assert uri == f"s3://{bucket}/runs/run-001/"

    s3 = aws_env.client("s3")
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=bucket)["Contents"]}
    assert "runs/run-001/manifest.json" in keys
    assert "runs/run-001/metrics/metrics.jsonl" in keys
    assert list_runs(aws_env, bucket) == ["run-001"]
