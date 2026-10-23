import json
import os
import tempfile

import pytest
from moto import mock_aws

from agent.features import build_features
from agent.synthetic import generate_run, write_jsonl
from heal.publisher import DetectorPublisher
from infra.aws import Aws
from infra.config import InfraConfig
from model.model import Detector

CFG = InfraConfig()


def _features(rows):
    path = os.path.join(tempfile.gettempdir(), f"pub_{id(rows)}.jsonl")
    write_jsonl(rows, path)
    try:
        return build_features(path)
    finally:
        os.remove(path)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    with mock_aws():
        aws = Aws(CFG)
        topic = aws.client("sns").create_topic(Name=CFG.name("alerts"))["TopicArn"]
        normal_rows, _ = generate_run(n_normal=300, n_fault=0, seed=1)
        detector = Detector(contamination=0.02).fit(_features(normal_rows))
        fault_rows, _ = generate_run(n_normal=50, n_fault=60, seed=2)
        yield aws, topic, detector, _features(normal_rows), _features(fault_rows)


def test_normal_does_not_publish(setup):
    aws, topic, detector, normal_feats, _ = setup
    pub = DetectorPublisher(aws, topic, detector, k=3, cooldown_s=0)
    assert pub.process(normal_feats) is False


def test_sustained_anomaly_publishes_after_k(setup):
    aws, topic, detector, _, fault_feats = setup
    pub = DetectorPublisher(aws, topic, detector, k=3, cooldown_s=0)
    results = [pub.process(fault_feats) for _ in range(3)]
    assert results == [False, False, True]   # fires only after k sustained


def test_cooldown_blocks_repeat(setup):
    aws, topic, detector, _, fault_feats = setup
    pub = DetectorPublisher(aws, topic, detector, k=1, cooldown_s=10_000)
    assert pub.process(fault_feats) is True     # k=1 -> immediate
    assert pub.process(fault_feats) is False    # cooldown blocks the repeat


def test_alert_reaches_sns_via_sqs(setup):
    aws, topic, detector, _, fault_feats = setup
    sqs = aws.client("sqs")
    queue_url = sqs.create_queue(QueueName="biop-test")["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    aws.client("sns").subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn)

    pub = DetectorPublisher(aws, topic, detector, k=1, cooldown_s=0)
    assert pub.process(fault_feats) is True

    msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1).get("Messages", [])
    assert msgs
    body = json.loads(msgs[0]["Body"])
    assert "anomaly_score" in json.loads(body["Message"])
