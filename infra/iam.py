import json

from botocore.exceptions import ClientError

from .aws import Aws, tag_list

SSM_MANAGED_POLICY = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
SSM_READ_POLICY_NAME = "biop-ssm-read"

_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}


def _swallow_missing(fn) -> None:
    try:
        fn()
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NoSuchEntity", "NoSuchEntityException"):
            raise


def ensure_instance_profile(aws: Aws) -> str:
    """Role + instance profile letting instances be managed by SSM (for chaos
    Run Command) and read the project's SSM parameters (the DB password)."""
    cfg = aws.config
    iam = aws.client("iam")
    role = cfg.name("ec2-role")
    profile = cfg.name("ec2-profile")

    try:
        iam.get_role(RoleName=role)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(_TRUST),
                        Tags=tag_list(cfg.tags))

    iam.attach_role_policy(RoleName=role, PolicyArn=SSM_MANAGED_POLICY)  # idempotent
    iam.put_role_policy(
        RoleName=role,
        PolicyName=SSM_READ_POLICY_NAME,
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["ssm:GetParameter", "ssm:GetParameters"],
                "Resource": f"arn:aws:ssm:{cfg.region}:*:parameter/{cfg.project}/*",
            }],
        }),
    )

    try:
        iam.get_instance_profile(InstanceProfileName=profile)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam.create_instance_profile(InstanceProfileName=profile, Tags=tag_list(cfg.tags))
        iam.add_role_to_instance_profile(InstanceProfileName=profile, RoleName=role)

    return profile


def delete_instance_profile(aws: Aws) -> None:
    cfg = aws.config
    iam = aws.client("iam")
    role = cfg.name("ec2-role")
    profile = cfg.name("ec2-profile")

    _swallow_missing(lambda: iam.remove_role_from_instance_profile(
        InstanceProfileName=profile, RoleName=role))
    _swallow_missing(lambda: iam.delete_instance_profile(InstanceProfileName=profile))
    _swallow_missing(lambda: iam.detach_role_policy(RoleName=role, PolicyArn=SSM_MANAGED_POLICY))
    _swallow_missing(lambda: iam.delete_role_policy(RoleName=role, PolicyName=SSM_READ_POLICY_NAME))
    _swallow_missing(lambda: iam.delete_role(RoleName=role))
