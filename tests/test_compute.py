import base64

import pytest
from moto import mock_aws

from infra.aws import Aws
from infra.compute import (
    delete_launch_template,
    ensure_launch_template,
    render_user_data,
)
from infra.config import InfraConfig
from infra.iam import (
    SSM_MANAGED_POLICY,
    delete_instance_profile,
    ensure_instance_profile,
)
from infra.network import ensure_security_groups

CFG = InfraConfig()


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")  # let moto know AWS-managed policies
    with mock_aws():
        aws = Aws(CFG)
        sg_ids, _ = ensure_security_groups(aws)
        # moto serves the public AL2023 AMI SSM parameter itself; no seeding needed.
        yield aws, sg_ids


def test_render_user_data_substitutes():
    ud = render_user_data(CFG, "biop-db.abc123.ap-south-1.rds.amazonaws.com")
    assert "__DB_ENDPOINT__" not in ud and "__APP_PORT__" not in ud
    assert "biop-db.abc123.ap-south-1.rds.amazonaws.com" in ud
    assert "gunicorn -w 2 -b 0.0.0.0:8000 run:app" in ud
    assert "agent.run_agent" in ud            # agent auto-starts
    assert "ssm get-parameter" in ud          # DB password pulled from SSM, not baked in


def test_instance_profile_has_ssm(aws_env):
    aws, _ = aws_env
    profile = ensure_instance_profile(aws)
    assert profile == CFG.name("ec2-profile")
    iam = aws.client("iam")
    attached = iam.list_attached_role_policies(RoleName=CFG.name("ec2-role"))["AttachedPolicies"]
    assert any(p["PolicyArn"] == SSM_MANAGED_POLICY for p in attached)
    inline = iam.list_role_policies(RoleName=CFG.name("ec2-role"))["PolicyNames"]
    assert "biop-ssm-read" in inline


def test_launch_template_config(aws_env):
    aws, sg_ids = aws_env
    profile = ensure_instance_profile(aws)
    lt = ensure_launch_template(aws, sg_ids["ec2"], profile,
                                "biop-db.abc.ap-south-1.rds.amazonaws.com")
    assert lt["LaunchTemplateName"] == CFG.name("lt")

    ec2 = aws.client("ec2")
    data = ec2.describe_launch_template_versions(
        LaunchTemplateName=CFG.name("lt"), Versions=["$Latest"]
    )["LaunchTemplateVersions"][0]["LaunchTemplateData"]
    assert data["InstanceType"] == CFG.instance_type
    assert sg_ids["ec2"] in data["SecurityGroupIds"]
    assert data["IamInstanceProfile"]["Name"] == profile
    decoded = base64.b64decode(data["UserData"]).decode()
    assert "DATABASE_URL=postgresql+psycopg" in decoded


def test_teardown(aws_env):
    aws, sg_ids = aws_env
    profile = ensure_instance_profile(aws)
    ensure_launch_template(aws, sg_ids["ec2"], profile, "biop-db.x.rds.amazonaws.com")
    delete_launch_template(aws)
    delete_instance_profile(aws)

    ec2 = aws.client("ec2")
    assert ec2.describe_launch_templates()["LaunchTemplates"] == []
