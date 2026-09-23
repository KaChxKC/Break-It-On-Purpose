import pytest
from moto import mock_aws

from infra.aws import Aws
from infra.config import InfraConfig
from infra.manage import down, status, up

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield Aws(CFG)


def test_up_brings_everything_online(aws_env):
    result = up(aws_env)
    assert result["alb_dns"]
    assert result["db_endpoint"]
    assert result["sns_topic"].endswith(CFG.name("alerts"))

    st = status(aws_env)
    assert all(st["security_groups"].values())
    assert st["rds"] == "available"
    assert st["asg"]["desired"] == CFG.asg_desired
    assert st["alb"] != "absent"


def test_up_is_idempotent(aws_env):
    up(aws_env)
    up(aws_env)  # must not raise or duplicate
    rds = aws_env.client("rds")
    assert len(rds.describe_db_instances()["DBInstances"]) == 1


def test_down_leaves_nothing(aws_env):
    up(aws_env)
    down(aws_env)
    st = status(aws_env)
    assert st["rds"] == "absent"
    assert st["asg"] == "absent"
    assert st["alb"] == "absent"
    assert not any(st["security_groups"].values())
