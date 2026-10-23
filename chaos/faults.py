"""Fault injectors — a free stand-in for AWS FIS, driven through SSM and the
AWS APIs. Every injector records the precise onset (and, where bounded, offset)
timestamp: those timestamps are the ground-truth labels the whole lead-time
analysis depends on, so they are captured at the moment the fault is triggered.
"""

import time
from dataclasses import dataclass, field

from infra.aws import Aws
from infra.network import _find_sg, get_default_vpc


@dataclass
class FaultResult:
    fault: str
    onset_epoch: float
    offset_epoch: float | None = None
    detail: dict = field(default_factory=dict)


def asg_instance_ids(aws: Aws) -> list[str]:
    groups = aws.client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=[aws.config.name("asg")])["AutoScalingGroups"]
    if not groups:
        return []
    return [i["InstanceId"] for i in groups[0]["Instances"]]


def cpu_stress(aws: Aws, instance_ids: list[str], duration_s: int = 60,
               workers: int = 1) -> FaultResult:
    onset = time.time()
    command_id = aws.client("ssm").send_command(
        InstanceIds=instance_ids,
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [f"stress-ng --cpu {workers} --timeout {duration_s}s"]},
    )["Command"]["CommandId"]
    return FaultResult("cpu_stress", onset, onset + duration_s,
                       {"command_id": command_id, "instance_ids": instance_ids,
                        "duration_s": duration_s})


def kill_instance(aws: Aws, instance_id: str | None = None) -> FaultResult:
    """Terminate one instance without decrementing desired capacity, so the ASG
    heals by launching a replacement."""
    autoscaling = aws.client("autoscaling")
    if instance_id is None:
        ids = asg_instance_ids(aws)
        if not ids:
            raise RuntimeError("no instances in the ASG to terminate")
        instance_id = ids[0]
    onset = time.time()
    autoscaling.terminate_instance_in_auto_scaling_group(
        InstanceId=instance_id, ShouldDecrementDesiredCapacity=False)
    return FaultResult("instance_kill", onset, None, {"instance_id": instance_id})


def db_sg_block(aws: Aws, duration_s: int = 60) -> FaultResult:
    """Deterministic DB fault: revoke the RDS ingress rules for a window, then
    restore them exactly. (Single-AZ has no standby, so this is a connectivity
    fault, not a failover.)"""
    cfg = aws.config
    ec2 = aws.client("ec2")
    vpc_id = get_default_vpc(ec2)
    rds_sg = _find_sg(ec2, cfg.name("rds-sg"), vpc_id)

    sg = ec2.describe_security_groups(GroupIds=[rds_sg])["SecurityGroups"][0]
    # Revoke the real stored rules so the spec always matches, and restore the
    # identical objects afterwards.
    rules = [p for p in sg["IpPermissions"] if p.get("FromPort") == cfg.db_port]

    onset = time.time()
    ec2.revoke_security_group_ingress(GroupId=rds_sg, IpPermissions=rules)
    time.sleep(duration_s)
    ec2.authorize_security_group_ingress(GroupId=rds_sg, IpPermissions=rules)
    return FaultResult("db_sg_block", onset, time.time(),
                       {"duration_s": duration_s, "rules_restored": len(rules)})


def db_reboot(aws: Aws) -> FaultResult:
    onset = time.time()
    aws.client("rds").reboot_db_instance(DBInstanceIdentifier=aws.config.name("db"))
    return FaultResult("db_reboot", onset, None, {})
