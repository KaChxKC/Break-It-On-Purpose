import boto3

from .config import InfraConfig


class Aws:
    """Thin wrapper over a boto3 session bound to the configured region.

    Credentials come from the environment / shared profile (aws configure) —
    never hardcoded. Clients are created on demand.
    """

    def __init__(self, config: InfraConfig, profile: str | None = None) -> None:
        self.config = config
        self.session = boto3.Session(profile_name=profile, region_name=config.region)

    def client(self, service: str):
        return self.session.client(service)


def tag_list(tags: dict) -> list[dict]:
    """Convert a {k: v} dict to boto3's [{"Key":k,"Value":v}] tag form."""
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def tag_spec(resource_type: str, tags: dict) -> list[dict]:
    return [{"ResourceType": resource_type, "Tags": tag_list(tags)}]
