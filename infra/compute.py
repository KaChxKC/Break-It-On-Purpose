import base64

from botocore.exceptions import ClientError

from .aws import Aws, tag_list
from .database import password_param_name

REPO_URL = "https://github.com/KaChxKC/Break-It-On-Purpose.git"
AL2023_AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

# Placeholders use __NAME__ so they can't collide with bash's own ${...}/{} syntax.
_USER_DATA = r"""#!/bin/bash
set -euxo pipefail
dnf install -y git python3.11 python3.11-pip stress-ng

TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

git clone __REPO__ /opt/app || (cd /opt/app && git pull)
cd /opt/app
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt gunicorn

DB_PW=$(aws ssm get-parameter --name __PW_PARAM__ --with-decryption --region __REGION__ --query Parameter.Value --output text)
mkdir -p /opt/app/data/raw
cat > /opt/app/.env <<EOF
DATABASE_URL=postgresql+psycopg://__DB_USER__:${DB_PW}@__DB_ENDPOINT__:__DB_PORT__/__DB_NAME__
INSTANCE_ID=${IID}
APP_HOST=0.0.0.0
APP_PORT=__APP_PORT__
EOF

cat > /etc/systemd/system/biop-app.service <<EOF
[Unit]
Description=biop app
After=network-online.target
[Service]
WorkingDirectory=/opt/app
EnvironmentFile=/opt/app/.env
ExecStart=/opt/app/.venv/bin/gunicorn -w 2 -b 0.0.0.0:__APP_PORT__ run:app
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/biop-agent.service <<EOF
[Unit]
Description=biop metric agent
After=biop-app.service
[Service]
WorkingDirectory=/opt/app
ExecStart=/opt/app/.venv/bin/python -m agent.run_agent --app-url http://127.0.0.1:__APP_PORT__/metrics --out /opt/app/data/raw/metrics.jsonl --interval 2
Restart=always
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now biop-app biop-agent
"""


def render_user_data(cfg, db_endpoint: str) -> str:
    subs = {
        "__REPO__": REPO_URL,
        "__REGION__": cfg.region,
        "__PW_PARAM__": password_param_name(cfg),
        "__DB_USER__": cfg.db_username,
        "__DB_ENDPOINT__": db_endpoint,
        "__DB_PORT__": str(cfg.db_port),
        "__DB_NAME__": cfg.db_name,
        "__APP_PORT__": str(cfg.app_port),
    }
    script = _USER_DATA
    for token, value in subs.items():
        script = script.replace(token, value)
    return script


def _resolve_ami(aws: Aws) -> str:
    return aws.client("ssm").get_parameter(Name=AL2023_AMI_PARAM)["Parameter"]["Value"]


def _find_launch_template(ec2, name: str):
    try:
        return ec2.describe_launch_templates(LaunchTemplateNames=[name])["LaunchTemplates"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("InvalidLaunchTemplateName.NotFoundException",
                                             "InvalidLaunchTemplateName.NotFound"):
            return None
        raise


def ensure_launch_template(aws: Aws, ec2_sg_id: str, instance_profile: str,
                           db_endpoint: str, ami: str | None = None) -> dict:
    cfg = aws.config
    ec2 = aws.client("ec2")
    template_data = {
        "ImageId": ami or _resolve_ami(aws),
        "InstanceType": cfg.instance_type,
        "IamInstanceProfile": {"Name": instance_profile},
        "SecurityGroupIds": [ec2_sg_id],
        "UserData": base64.b64encode(render_user_data(cfg, db_endpoint).encode()).decode(),
        "TagSpecifications": [{"ResourceType": "instance",
                               "Tags": tag_list({**cfg.tags, "Name": cfg.name("app")})}],
    }
    name = cfg.name("lt")
    if _find_launch_template(ec2, name):
        # Roll a new default version rather than mutating in place. DefaultVersion
        # must be a concrete number ($Latest is not accepted here).
        version = ec2.create_launch_template_version(
            LaunchTemplateName=name, LaunchTemplateData=template_data
        )["LaunchTemplateVersion"]["VersionNumber"]
        ec2.modify_launch_template(LaunchTemplateName=name, DefaultVersion=str(version))
        return _find_launch_template(ec2, name)

    return ec2.create_launch_template(
        LaunchTemplateName=name,
        LaunchTemplateData=template_data,
        TagSpecifications=[{"ResourceType": "launch-template", "Tags": tag_list(cfg.tags)}],
    )["LaunchTemplate"]


def delete_launch_template(aws: Aws) -> None:
    ec2 = aws.client("ec2")
    try:
        ec2.delete_launch_template(LaunchTemplateName=aws.config.name("lt"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("InvalidLaunchTemplateName.NotFoundException",
                                                 "InvalidLaunchTemplateName.NotFound"):
            raise
