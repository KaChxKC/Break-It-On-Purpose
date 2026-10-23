"""The action end of the self-healing loop: an SNS-subscribed Lambda that reacts
to an anomaly alert by pre-emptively scaling the ASG out (or restarting the app
via SSM). Auto Scaling and SNS+Lambda are effectively free at this volume.
"""

import io
import json
import zipfile

from botocore.exceptions import ClientError

from infra.aws import Aws, tag_list
from infra.messaging import ensure_topic

LAMBDA_BASIC_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
_INLINE_POLICY_NAME = "biop-heal"

_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

# Reacts to an alert: scale out by one (bounded by MaxSize), honouring the ASG
# cooldown so a burst of alerts can't runaway-scale. ACTION=restart instead
# bounces the app via SSM.
_HANDLER_SRC = """import os
import boto3


def handler(event, context):
    name = os.environ["ASG_NAME"]
    action = os.environ.get("ACTION", "scale_out")
    asg = boto3.client("autoscaling")
    group = asg.describe_auto_scaling_groups(
        AutoScalingGroupNames=[name])["AutoScalingGroups"][0]

    if action == "restart":
        ids = [i["InstanceId"] for i in group["Instances"]]
        if ids:
            boto3.client("ssm").send_command(
                InstanceIds=ids, DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["systemctl restart biop-app"]})
        return {"action": "restart", "instances": ids}

    new_desired = min(group["DesiredCapacity"] + 1, group["MaxSize"])
    try:
        asg.set_desired_capacity(AutoScalingGroupName=name,
                                 DesiredCapacity=new_desired, HonorCooldown=True)
    except asg.exceptions.ScalingActivityInProgressFault:
        return {"action": "scale_out", "skipped": "cooldown"}
    return {"action": "scale_out", "desired": new_desired}
"""


def _zip_handler() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("handler.py", _HANDLER_SRC)
    return buffer.getvalue()


def _ensure_role(aws: Aws) -> str:
    cfg = aws.config
    iam = aws.client("iam")
    role = cfg.name("heal-role")
    try:
        arn = iam.get_role(RoleName=role)["Role"]["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(_TRUST),
                              Tags=tag_list(cfg.tags))["Role"]["Arn"]
    iam.attach_role_policy(RoleName=role, PolicyArn=LAMBDA_BASIC_POLICY)
    iam.put_role_policy(RoleName=role, PolicyName=_INLINE_POLICY_NAME, PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["autoscaling:DescribeAutoScalingGroups",
                       "autoscaling:SetDesiredCapacity", "ssm:SendCommand"],
            "Resource": "*",
        }],
    }))
    return arn


def ensure_responder(aws: Aws, action: str = "scale_out") -> dict:
    cfg = aws.config
    role_arn = _ensure_role(aws)
    lam = aws.client("lambda")
    sns = aws.client("sns")
    fn_name = cfg.name("responder")
    topic_arn = ensure_topic(aws)

    try:
        lam.get_function(FunctionName=fn_name)
        lam.update_function_code(FunctionName=fn_name, ZipFile=_zip_handler())
        fn_arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        fn_arn = lam.create_function(
            FunctionName=fn_name, Runtime="python3.12", Role=role_arn,
            Handler="handler.handler", Code={"ZipFile": _zip_handler()}, Timeout=30,
            Environment={"Variables": {"ASG_NAME": cfg.name("asg"), "ACTION": action}},
            Tags=cfg.tags,
        )["FunctionArn"]

    try:
        lam.add_permission(FunctionName=fn_name, StatementId="sns-invoke",
                           Action="lambda:InvokeFunction", Principal="sns.amazonaws.com",
                           SourceArn=topic_arn)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    sns.subscribe(TopicArn=topic_arn, Protocol="lambda", Endpoint=fn_arn)
    return {"function_arn": fn_arn, "topic_arn": topic_arn}


def delete_responder(aws: Aws) -> None:
    cfg = aws.config
    lam, sns, iam = aws.client("lambda"), aws.client("sns"), aws.client("iam")

    try:
        topic_arn = ensure_topic(aws)
        fn_arn = lam.get_function(FunctionName=cfg.name("responder"))["Configuration"]["FunctionArn"]
        for sub in sns.list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]:
            if sub["Endpoint"] == fn_arn and sub["SubscriptionArn"].startswith("arn:"):
                sns.unsubscribe(SubscriptionArn=sub["SubscriptionArn"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    try:
        lam.delete_function(FunctionName=cfg.name("responder"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    role = cfg.name("heal-role")
    for call in (
        lambda: iam.detach_role_policy(RoleName=role, PolicyArn=LAMBDA_BASIC_POLICY),
        lambda: iam.delete_role_policy(RoleName=role, PolicyName=_INLINE_POLICY_NAME),
        lambda: iam.delete_role(RoleName=role),
    ):
        try:
            call()
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchEntity":
                raise
