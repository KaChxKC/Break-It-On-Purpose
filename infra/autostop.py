import io
import json
import zipfile

from botocore.exceptions import ClientError

from .aws import Aws, tag_list

LAMBDA_BASIC_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
_INLINE_POLICY_NAME = "biop-autostop"

_LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

# Handler that runs on the schedule: scale the ASG to zero and stop RDS.
_HANDLER_SRC = """import os
import boto3


def handler(event, context):
    boto3.client("autoscaling").update_auto_scaling_group(
        AutoScalingGroupName=os.environ["ASG_NAME"], MinSize=0, DesiredCapacity=0)
    try:
        boto3.client("rds").stop_db_instance(DBInstanceIdentifier=os.environ["DB_ID"])
    except boto3.client("rds").exceptions.InvalidDBInstanceStateFault:
        pass  # already stopped
    return {"stopped": True}
"""


def _zip_handler() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("handler.py", _HANDLER_SRC)
    return buffer.getvalue()


def ensure_lambda_role(aws: Aws) -> str:
    cfg = aws.config
    iam = aws.client("iam")
    role = cfg.name("autostop-role")
    try:
        arn = iam.get_role(RoleName=role)["Role"]["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(_LAMBDA_TRUST),
                              Tags=tag_list(cfg.tags))["Role"]["Arn"]
    iam.attach_role_policy(RoleName=role, PolicyArn=LAMBDA_BASIC_POLICY)
    iam.put_role_policy(RoleName=role, PolicyName=_INLINE_POLICY_NAME, PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["autoscaling:UpdateAutoScalingGroup", "rds:StopDBInstance"],
            "Resource": "*",
        }],
    }))
    return arn


def ensure_autostop(aws: Aws) -> dict:
    """Create the scheduled auto-stop Lambda + EventBridge rule."""
    cfg = aws.config
    role_arn = ensure_lambda_role(aws)
    lam = aws.client("lambda")
    events = aws.client("events")
    fn_name = cfg.name("autostop")

    try:
        lam.get_function(FunctionName=fn_name)
        lam.update_function_code(FunctionName=fn_name, ZipFile=_zip_handler())
        fn_arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        fn_arn = lam.create_function(
            FunctionName=fn_name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": _zip_handler()},
            Timeout=30,
            Environment={"Variables": {"ASG_NAME": cfg.name("asg"), "DB_ID": cfg.name("db")}},
            Tags=cfg.tags,
        )["FunctionArn"]

    rule_arn = events.put_rule(
        Name=cfg.name("nightly-stop"),
        ScheduleExpression=cfg.autostop_cron,
        State="ENABLED",
    )["RuleArn"]
    try:
        lam.add_permission(
            FunctionName=fn_name, StatementId="events-invoke",
            Action="lambda:InvokeFunction", Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    events.put_targets(Rule=cfg.name("nightly-stop"), Targets=[{"Id": "autostop", "Arn": fn_arn}])
    return {"function_arn": fn_arn, "rule_arn": rule_arn}


def delete_autostop(aws: Aws) -> None:
    cfg = aws.config
    events, lam, iam = aws.client("events"), aws.client("lambda"), aws.client("iam")
    rule = cfg.name("nightly-stop")

    try:
        events.remove_targets(Rule=rule, Ids=["autostop"])
        events.delete_rule(Name=rule)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    try:
        lam.delete_function(FunctionName=cfg.name("autostop"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    role = cfg.name("autostop-role")
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
