from botocore.exceptions import ClientError

from .aws import Aws, tag_spec


def get_default_vpc(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("No default VPC in this region; create one first.")
    return vpcs[0]["VpcId"]


def get_default_subnets(ec2, vpc_id: str) -> list[str]:
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    return [s["SubnetId"] for s in subnets]


def _find_sg(ec2, name: str, vpc_id: str) -> str | None:
    found = ec2.describe_security_groups(Filters=[
        {"Name": "group-name", "Values": [name]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ])["SecurityGroups"]
    return found[0]["GroupId"] if found else None


def _ensure_sg(ec2, cfg, key: str, vpc_id: str) -> str:
    existing = _find_sg(ec2, cfg.name(key), vpc_id)
    if existing:
        return existing
    return ec2.create_security_group(
        GroupName=cfg.name(key),
        Description=f"{cfg.project} {key}",
        VpcId=vpc_id,
        TagSpecifications=tag_spec("security-group", {**cfg.tags, "Name": cfg.name(key)}),
    )["GroupId"]


def _authorize(ec2, group_id: str, permissions: list[dict]) -> None:
    try:
        ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=permissions)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise  # rule already present is fine; anything else is not


def ensure_security_groups(aws: Aws) -> tuple[dict, str]:
    """Create (or reuse) the three-tier SG chain. Returns ({alb,ec2,rds}, vpc_id)."""
    cfg = aws.config
    ec2 = aws.client("ec2")
    vpc_id = get_default_vpc(ec2)

    alb = _ensure_sg(ec2, cfg, "alb-sg", vpc_id)
    ec2_sg = _ensure_sg(ec2, cfg, "ec2-sg", vpc_id)
    rds = _ensure_sg(ec2, cfg, "rds-sg", vpc_id)

    # public -> ALB
    _authorize(ec2, alb, [{
        "IpProtocol": "tcp", "FromPort": cfg.alb_port, "ToPort": cfg.alb_port,
        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "public HTTP"}],
    }])
    # ALB -> EC2 app port
    _authorize(ec2, ec2_sg, [{
        "IpProtocol": "tcp", "FromPort": cfg.app_port, "ToPort": cfg.app_port,
        "UserIdGroupPairs": [{"GroupId": alb, "Description": "from ALB"}],
    }])
    # EC2 -> RDS (only EC2 may reach the database)
    _authorize(ec2, rds, [{
        "IpProtocol": "tcp", "FromPort": cfg.db_port, "ToPort": cfg.db_port,
        "UserIdGroupPairs": [{"GroupId": ec2_sg, "Description": "from EC2"}],
    }])
    return {"alb": alb, "ec2": ec2_sg, "rds": rds}, vpc_id


def delete_security_groups(aws: Aws) -> None:
    """Delete the SG chain in dependency order (rds -> ec2 -> alb), since each
    tier is referenced by the one before it."""
    cfg = aws.config
    ec2 = aws.client("ec2")
    vpc_id = get_default_vpc(ec2)
    for key in ("rds-sg", "ec2-sg", "alb-sg"):
        sg_id = _find_sg(ec2, cfg.name(key), vpc_id)
        if not sg_id:
            continue
        try:
            ec2.delete_security_group(GroupId=sg_id)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidGroup.NotFound":
                raise
