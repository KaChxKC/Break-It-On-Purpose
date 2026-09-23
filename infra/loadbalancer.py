from botocore.exceptions import ClientError

from .aws import Aws, tag_list

_LB_NOT_FOUND = ("LoadBalancerNotFound", "LoadBalancerNotFoundException")
_TG_NOT_FOUND = ("TargetGroupNotFound", "TargetGroupNotFoundException")


def _find_lb(elb, name: str):
    try:
        return elb.describe_load_balancers(Names=[name])["LoadBalancers"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _LB_NOT_FOUND:
            return None
        raise


def _find_tg(elb, name: str):
    try:
        return elb.describe_target_groups(Names=[name])["TargetGroups"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _TG_NOT_FOUND:
            return None
        raise


def ensure_load_balancer(aws: Aws, alb_sg_id: str, subnet_ids: list[str]) -> dict:
    cfg = aws.config
    elb = aws.client("elbv2")
    existing = _find_lb(elb, cfg.name("alb"))
    if existing:
        return existing
    return elb.create_load_balancer(
        Name=cfg.name("alb"),
        Subnets=subnet_ids,
        SecurityGroups=[alb_sg_id],
        Scheme="internet-facing",
        Type="application",
        Tags=tag_list({**cfg.tags, "Name": cfg.name("alb")}),
    )["LoadBalancers"][0]


def ensure_target_group(aws: Aws, vpc_id: str) -> dict:
    cfg = aws.config
    elb = aws.client("elbv2")
    existing = _find_tg(elb, cfg.name("tg"))
    if existing:
        return existing
    return elb.create_target_group(
        Name=cfg.name("tg"),
        Protocol="HTTP",
        Port=cfg.app_port,
        VpcId=vpc_id,
        TargetType="instance",
        HealthCheckProtocol="HTTP",
        HealthCheckPath="/health",
        HealthCheckIntervalSeconds=15,
        HealthyThresholdCount=2,
        UnhealthyThresholdCount=3,
        Matcher={"HttpCode": "200"},
        Tags=tag_list({**cfg.tags, "Name": cfg.name("tg")}),
    )["TargetGroups"][0]


def ensure_listener(aws: Aws, lb_arn: str, tg_arn: str) -> dict:
    cfg = aws.config
    elb = aws.client("elbv2")
    for listener in elb.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"]:
        if listener["Port"] == cfg.alb_port:
            return listener
    return elb.create_listener(
        LoadBalancerArn=lb_arn,
        Protocol="HTTP",
        Port=cfg.alb_port,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )["Listeners"][0]


def ensure_alb(aws: Aws, alb_sg_id: str, subnet_ids: list[str], vpc_id: str) -> dict:
    lb = ensure_load_balancer(aws, alb_sg_id, subnet_ids)
    tg = ensure_target_group(aws, vpc_id)
    ensure_listener(aws, lb["LoadBalancerArn"], tg["TargetGroupArn"])
    return {"lb_arn": lb["LoadBalancerArn"], "tg_arn": tg["TargetGroupArn"], "dns": lb["DNSName"]}


def delete_alb(aws: Aws) -> None:
    cfg = aws.config
    elb = aws.client("elbv2")
    lb = _find_lb(elb, cfg.name("alb"))
    if lb:
        for listener in elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]:
            elb.delete_listener(ListenerArn=listener["ListenerArn"])
        elb.delete_load_balancer(LoadBalancerArn=lb["LoadBalancerArn"])

    tg = _find_tg(elb, cfg.name("tg"))
    if tg:
        try:
            elb.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUse":
                raise  # still attached to the (async-deleting) LB; retry later
