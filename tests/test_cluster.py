import pytest
from moto import mock_aws

from infra.aws import Aws
from infra.compute import ensure_launch_template
from infra.config import InfraConfig
from infra.iam import ensure_instance_profile
from infra.loadbalancer import delete_alb, ensure_alb
from infra.network import ensure_security_groups, get_default_subnets, get_default_vpc
from infra.scaling import delete_asg, ensure_asg, set_desired_capacity

CFG = InfraConfig()


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", CFG.region)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        aws = Aws(CFG)
        sg_ids, vpc_id = ensure_security_groups(aws)
        subnets = get_default_subnets(aws.client("ec2"), vpc_id)
        profile = ensure_instance_profile(aws)
        ensure_launch_template(aws, sg_ids["ec2"], profile, "biop-db.x.rds.amazonaws.com")
        yield aws, sg_ids, subnets, vpc_id


def test_alb_target_group_and_listener(cluster):
    aws, sg_ids, subnets, vpc_id = cluster
    out = ensure_alb(aws, sg_ids["alb"], subnets, vpc_id)
    elb = aws.client("elbv2")

    tg = elb.describe_target_groups(TargetGroupArns=[out["tg_arn"]])["TargetGroups"][0]
    assert tg["HealthCheckPath"] == "/health"
    assert tg["Port"] == CFG.app_port

    listeners = elb.describe_listeners(LoadBalancerArn=out["lb_arn"])["Listeners"]
    assert any(l["Port"] == CFG.alb_port for l in listeners)


def test_asg_wired_to_template_and_tg(cluster):
    aws, sg_ids, subnets, vpc_id = cluster
    out = ensure_alb(aws, sg_ids["alb"], subnets, vpc_id)
    group = ensure_asg(aws, CFG.name("lt"), out["tg_arn"], subnets)
    assert group["MinSize"] == CFG.asg_min
    assert group["MaxSize"] == CFG.asg_max
    assert group["DesiredCapacity"] == CFG.asg_desired
    assert group["LaunchTemplate"]["LaunchTemplateName"] == CFG.name("lt")
    assert out["tg_arn"] in group["TargetGroupARNs"]


def test_set_desired_capacity_to_zero(cluster):
    aws, sg_ids, subnets, vpc_id = cluster
    out = ensure_alb(aws, sg_ids["alb"], subnets, vpc_id)
    ensure_asg(aws, CFG.name("lt"), out["tg_arn"], subnets)
    set_desired_capacity(aws, 0)  # nightly auto-stop path
    autoscaling = aws.client("autoscaling")
    group = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[CFG.name("asg")])["AutoScalingGroups"][0]
    assert group["DesiredCapacity"] == 0
    assert group["MinSize"] == 0


def test_teardown(cluster):
    aws, sg_ids, subnets, vpc_id = cluster
    out = ensure_alb(aws, sg_ids["alb"], subnets, vpc_id)
    ensure_asg(aws, CFG.name("lt"), out["tg_arn"], subnets)
    delete_asg(aws)
    delete_alb(aws)

    autoscaling = aws.client("autoscaling")
    assert autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[CFG.name("asg")])["AutoScalingGroups"] == []
    elb = aws.client("elbv2")
    assert elb.describe_load_balancers()["LoadBalancers"] == []
