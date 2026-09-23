from .aws import Aws, tag_list


def _find_topic_arn(sns, name: str) -> str | None:
    paginator = sns.get_paginator("list_topics")
    for page in paginator.paginate():
        for topic in page["Topics"]:
            if topic["TopicArn"].rsplit(":", 1)[-1] == name:
                return topic["TopicArn"]
    return None


def ensure_topic(aws: Aws) -> str:
    """SNS topic the detector publishes to in Phase 6. create_topic is
    idempotent — it returns the existing ARN for the same name."""
    cfg = aws.config
    sns = aws.client("sns")
    return sns.create_topic(
        Name=cfg.name("alerts"),
        Tags=tag_list({**cfg.tags, "Name": cfg.name("alerts")}),
    )["TopicArn"]


def delete_topic(aws: Aws) -> None:
    sns = aws.client("sns")
    arn = _find_topic_arn(sns, aws.config.name("alerts"))
    if arn:
        sns.delete_topic(TopicArn=arn)
