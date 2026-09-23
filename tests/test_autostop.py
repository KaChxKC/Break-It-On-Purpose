import pytest
from moto import mock_aws

from infra.autostop import delete_autostop, ensure_autostop
from infra.aws import Aws
from infra.config import InfraConfig
from infra.messaging import delete_topic, ensure_topic

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield Aws(CFG)


def test_sns_topic_ensure_and_delete(aws_env):
    arn = ensure_topic(aws_env)
    assert arn.endswith(CFG.name("alerts"))
    assert ensure_topic(aws_env) == arn        # idempotent
    delete_topic(aws_env)
    sns = aws_env.client("sns")
    assert all(not t["TopicArn"].endswith(CFG.name("alerts"))
               for t in sns.list_topics()["Topics"])


def test_autostop_lambda_and_schedule(aws_env):
    out = ensure_autostop(aws_env)
    lam = aws_env.client("lambda")
    fn = lam.get_function(FunctionName=CFG.name("autostop"))
    env = fn["Configuration"]["Environment"]["Variables"]
    assert env["ASG_NAME"] == CFG.name("asg")
    assert env["DB_ID"] == CFG.name("db")

    events = aws_env.client("events")
    rule = events.describe_rule(Name=CFG.name("nightly-stop"))
    assert rule["ScheduleExpression"] == CFG.autostop_cron
    targets = events.list_targets_by_rule(Rule=CFG.name("nightly-stop"))["Targets"]
    assert targets[0]["Arn"] == out["function_arn"]


def test_autostop_idempotent_then_delete(aws_env):
    ensure_autostop(aws_env)
    ensure_autostop(aws_env)  # must not raise
    delete_autostop(aws_env)
    lam = aws_env.client("lambda")
    assert all(f["FunctionName"] != CFG.name("autostop")
               for f in lam.list_functions()["Functions"])
