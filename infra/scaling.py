from .aws import Aws


def _find_asg(autoscaling, name: str):
    groups = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[name])["AutoScalingGroups"]
    return groups[0] if groups else None


def ensure_asg(aws: Aws, launch_template_name: str, tg_arn: str,
               subnet_ids: list[str]) -> dict:
    cfg = aws.config
    autoscaling = aws.client("autoscaling")
    name = cfg.name("asg")

    existing = _find_asg(autoscaling, name)
    if existing:
        return existing

    autoscaling.create_auto_scaling_group(
        AutoScalingGroupName=name,
        LaunchTemplate={"LaunchTemplateName": launch_template_name, "Version": "$Latest"},
        MinSize=cfg.asg_min,
        MaxSize=cfg.asg_max,
        DesiredCapacity=cfg.asg_desired,
        VPCZoneIdentifier=",".join(subnet_ids),
        TargetGroupARNs=[tg_arn],
        HealthCheckType="ELB",
        HealthCheckGracePeriod=120,
        Tags=[{"Key": k, "Value": v, "PropagateAtLaunch": True}
              for k, v in {**cfg.tags, "Name": cfg.name("app")}.items()],
    )
    return _find_asg(autoscaling, name)


def set_desired_capacity(aws: Aws, desired: int, min_size: int | None = None) -> None:
    """Scale the ASG. Used by the nightly auto-stop (desired=0) and the Phase 6
    pre-emptive scale-out. MinSize is lowered when needed so desired can reach 0."""
    autoscaling = aws.client("autoscaling")
    kwargs = {"AutoScalingGroupName": aws.config.name("asg"), "DesiredCapacity": desired}
    kwargs["MinSize"] = min_size if min_size is not None else min(desired, aws.config.asg_min)
    autoscaling.update_auto_scaling_group(**kwargs)


def delete_asg(aws: Aws) -> None:
    autoscaling = aws.client("autoscaling")
    if _find_asg(autoscaling, aws.config.name("asg")):
        # ForceDelete terminates the instances too, so nothing is left running.
        autoscaling.delete_auto_scaling_group(
            AutoScalingGroupName=aws.config.name("asg"), ForceDelete=True)
