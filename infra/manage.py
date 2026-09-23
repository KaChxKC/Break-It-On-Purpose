"""One-command lifecycle for the whole lab.

  python -m infra.manage up      # create everything, print the ALB DNS
  python -m infra.manage status  # show what's running
  python -m infra.manage down    # tear it all down (leaves nothing billing)

Resources are created in dependency order and destroyed in reverse. `down` is
idempotent — re-run it to sweep up anything that was still deleting.
"""

import argparse

from botocore.exceptions import ClientError

from .autostop import delete_autostop, ensure_autostop
from .aws import Aws
from .compute import delete_launch_template, ensure_launch_template
from .config import InfraConfig
from .database import delete_database, ensure_database
from .iam import delete_instance_profile, ensure_instance_profile
from .loadbalancer import delete_alb, ensure_alb
from .messaging import delete_topic, ensure_topic
from .network import (
    _find_sg,
    delete_security_groups,
    ensure_security_groups,
    get_default_subnets,
    get_default_vpc,
)
from .scaling import delete_asg, ensure_asg


def up(aws: Aws) -> dict:
    cfg = aws.config
    sg_ids, vpc_id = ensure_security_groups(aws)
    subnets = get_default_subnets(aws.client("ec2"), vpc_id)

    # RDS must be available before the launch template can bake in its endpoint.
    db = ensure_database(aws, sg_ids["rds"], wait=True)
    endpoint = db["Endpoint"]["Address"]

    profile = ensure_instance_profile(aws)
    ensure_launch_template(aws, sg_ids["ec2"], profile, endpoint)
    alb = ensure_alb(aws, sg_ids["alb"], subnets, vpc_id)
    ensure_asg(aws, cfg.name("lt"), alb["tg_arn"], subnets)
    topic = ensure_topic(aws)
    ensure_autostop(aws)
    return {"alb_dns": alb["dns"], "db_endpoint": endpoint, "sns_topic": topic}


def down(aws: Aws, wait: bool = True) -> None:
    # Reverse dependency order. Each step is best-effort/idempotent.
    delete_autostop(aws)
    delete_asg(aws)          # ForceDelete terminates the instances
    delete_alb(aws)
    delete_launch_template(aws)
    delete_instance_profile(aws)
    delete_database(aws, wait=wait)
    delete_topic(aws)
    delete_security_groups(aws)


def status(aws: Aws) -> dict:
    cfg = aws.config
    ec2 = aws.client("ec2")
    out: dict = {}

    vpc_id = get_default_vpc(ec2)
    out["security_groups"] = {
        key: bool(_find_sg(ec2, cfg.name(key), vpc_id))
        for key in ("alb-sg", "ec2-sg", "rds-sg")
    }

    try:
        db = aws.client("rds").describe_db_instances(
            DBInstanceIdentifier=cfg.name("db"))["DBInstances"][0]
        out["rds"] = db["DBInstanceStatus"]
    except ClientError:
        out["rds"] = "absent"

    groups = aws.client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[cfg.name("asg")])["AutoScalingGroups"]
    out["asg"] = ({"desired": groups[0]["DesiredCapacity"],
                   "instances": len(groups[0]["Instances"])} if groups else "absent")

    try:
        lb = aws.client("elbv2").describe_load_balancers(
            Names=[cfg.name("alb")])["LoadBalancers"][0]
        out["alb"] = lb.get("DNSName")
    except ClientError:
        out["alb"] = "absent"

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Break It On Purpose infra lifecycle")
    parser.add_argument("command", choices=["up", "down", "status"])
    parser.add_argument("--profile", default=None, help="AWS profile name")
    parser.add_argument("--no-wait", action="store_true",
                        help="don't wait for RDS deletion on down")
    args = parser.parse_args()

    aws = Aws(InfraConfig(), profile=args.profile)

    if args.command == "up":
        result = up(aws)
        print("UP complete")
        print(f"  ALB DNS     : http://{result['alb_dns']}")
        print(f"  DB endpoint : {result['db_endpoint']}")
        print(f"  SNS topic   : {result['sns_topic']}")
    elif args.command == "down":
        down(aws, wait=not args.no_wait)
        print("DOWN complete — nothing left billing")
    else:
        for key, value in status(aws).items():
            print(f"  {key:16}: {value}")


if __name__ == "__main__":
    main()
