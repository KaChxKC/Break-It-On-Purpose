import boto3
import pytest
from moto import mock_aws

from infra.aws import Aws
from infra.config import InfraConfig
from infra.network import (
    delete_security_groups,
    ensure_security_groups,
    get_default_subnets,
    get_default_vpc,
)

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    with mock_aws():
        yield Aws(CFG)


def _ingress(ec2, sg_id):
    sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
    return sg["IpPermissions"]


def test_creates_three_tier_chain(aws_env):
    ids, vpc_id = ensure_security_groups(aws_env)
    assert set(ids) == {"alb", "ec2", "rds"}

    ec2 = aws_env.client("ec2")
    # EC2 accepts the app port only from the ALB SG
    ec2_rules = _ingress(ec2, ids["ec2"])
    assert any(
        r["FromPort"] == CFG.app_port
        and any(p["GroupId"] == ids["alb"] for p in r.get("UserIdGroupPairs", []))
        for r in ec2_rules
    )
    # RDS accepts the db port only from the EC2 SG (never the public internet)
    rds_rules = _ingress(ec2, ids["rds"])
    assert any(
        r["FromPort"] == CFG.db_port
        and any(p["GroupId"] == ids["ec2"] for p in r.get("UserIdGroupPairs", []))
        for r in rds_rules
    )
    assert all(not r.get("IpRanges") for r in rds_rules)  # no CIDR exposure on the DB


def test_idempotent(aws_env):
    ids1, _ = ensure_security_groups(aws_env)
    ids2, _ = ensure_security_groups(aws_env)  # must not raise on duplicate rules
    assert ids1 == ids2


def test_delete_removes_groups(aws_env):
    ensure_security_groups(aws_env)
    delete_security_groups(aws_env)
    ec2 = aws_env.client("ec2")
    vpc_id = get_default_vpc(ec2)
    remaining = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "group-name", "Values": [CFG.name("rds-sg"),
                                                    CFG.name("ec2-sg"),
                                                    CFG.name("alb-sg")]}]
    )["SecurityGroups"]
    assert remaining == []


def test_default_vpc_and_subnets(aws_env):
    ec2 = aws_env.client("ec2")
    vpc_id = get_default_vpc(ec2)
    assert get_default_subnets(ec2, vpc_id)  # non-empty
