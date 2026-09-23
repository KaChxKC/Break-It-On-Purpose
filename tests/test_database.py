import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from infra.aws import Aws
from infra.config import InfraConfig
from infra.database import delete_database, ensure_database, password_param_name
from infra.network import ensure_security_groups

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    with mock_aws():
        aws = Aws(CFG)
        sg_ids, _ = ensure_security_groups(aws)
        yield aws, sg_ids


def test_single_az_private_and_sg_bound(aws_env):
    aws, sg_ids = aws_env
    db = ensure_database(aws, sg_ids["rds"])
    assert db["Engine"] == "postgres"
    assert db["MultiAZ"] is False                 # Single-AZ, no standby
    assert db["PubliclyAccessible"] is False
    bound = {g["VpcSecurityGroupId"] for g in db["VpcSecurityGroups"]}
    assert sg_ids["rds"] in bound


def test_password_stored_in_ssm(aws_env):
    aws, sg_ids = aws_env
    ensure_database(aws, sg_ids["rds"])
    ssm = aws.client("ssm")
    param = ssm.get_parameter(Name=password_param_name(CFG), WithDecryption=True)
    assert param["Parameter"]["Type"] == "SecureString"
    assert len(param["Parameter"]["Value"]) >= 16


def test_idempotent(aws_env):
    aws, sg_ids = aws_env
    ensure_database(aws, sg_ids["rds"])
    ensure_database(aws, sg_ids["rds"])
    rds = aws.client("rds")
    instances = rds.describe_db_instances()["DBInstances"]
    assert len([i for i in instances if i["DBInstanceIdentifier"] == CFG.name("db")]) == 1


def test_delete_removes_instance_and_param(aws_env):
    aws, sg_ids = aws_env
    ensure_database(aws, sg_ids["rds"])
    delete_database(aws)
    rds = aws.client("rds")
    with pytest.raises(ClientError) as err:
        rds.describe_db_instances(DBInstanceIdentifier=CFG.name("db"))
    assert err.value.response["Error"]["Code"] == "DBInstanceNotFound"
