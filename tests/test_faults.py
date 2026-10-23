import pytest
from moto import mock_aws

from chaos.faults import (
    asg_instance_ids,
    cpu_stress,
    db_reboot,
    db_sg_block,
    kill_instance,
)
from infra.aws import Aws
from infra.config import InfraConfig
from infra.manage import up
from infra.network import _find_sg, get_default_vpc

CFG = InfraConfig()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        aws = Aws(CFG)
        up(aws)
        yield aws


def test_asg_has_instances(live):
    assert len(asg_instance_ids(live)) == CFG.asg_desired


def test_cpu_stress_records_window(live):
    ids = asg_instance_ids(live)
    result = cpu_stress(live, ids, duration_s=30)
    assert result.fault == "cpu_stress"
    assert result.offset_epoch - result.onset_epoch == 30
    assert result.detail["command_id"]


def test_kill_instance(live):
    result = kill_instance(live)
    assert result.fault == "instance_kill"
    assert result.detail["instance_id"] in (asg_instance_ids(live) + [result.detail["instance_id"]])


def test_db_sg_block_restores_rule(live):
    result = db_sg_block(live, duration_s=0)
    assert result.fault == "db_sg_block"
    assert result.offset_epoch >= result.onset_epoch
    # the EC2->RDS rule must be back in place after the block window
    ec2 = live.client("ec2")
    vpc_id = get_default_vpc(ec2)
    rds_sg = _find_sg(ec2, CFG.name("rds-sg"), vpc_id)
    ec2_sg = _find_sg(ec2, CFG.name("ec2-sg"), vpc_id)
    sg = ec2.describe_security_groups(GroupIds=[rds_sg])["SecurityGroups"][0]
    assert any(
        p["FromPort"] == CFG.db_port
        and any(g["GroupId"] == ec2_sg for g in p.get("UserIdGroupPairs", []))
        for p in sg["IpPermissions"]
    )


def test_db_reboot(live):
    result = db_reboot(live)
    assert result.fault == "db_reboot"
    assert result.onset_epoch > 0
