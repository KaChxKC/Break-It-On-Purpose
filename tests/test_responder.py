import pytest
from moto import mock_aws

from heal.responder import delete_responder, ensure_responder
from infra.aws import Aws
from infra.config import InfraConfig

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield Aws(CFG)


def test_responder_lambda_and_subscription(aws_env):
    out = ensure_responder(aws_env, action="scale_out")
    lam = aws_env.client("lambda")
    env = lam.get_function(FunctionName=CFG.name("responder"))["Configuration"]["Environment"]["Variables"]
    assert env["ASG_NAME"] == CFG.name("asg")
    assert env["ACTION"] == "scale_out"

    sns = aws_env.client("sns")
    subs = sns.list_subscriptions_by_topic(TopicArn=out["topic_arn"])["Subscriptions"]
    assert any(s["Protocol"] == "lambda" and s["Endpoint"] == out["function_arn"] for s in subs)


def test_idempotent_then_delete(aws_env):
    ensure_responder(aws_env)
    ensure_responder(aws_env)  # must not raise
    delete_responder(aws_env)
    lam = aws_env.client("lambda")
    assert all(f["FunctionName"] != CFG.name("responder")
               for f in lam.list_functions()["Functions"])
