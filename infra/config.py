from dataclasses import dataclass


@dataclass(frozen=True)
class InfraConfig:
    """All knobs for the lab in one place. Every resource is named and tagged
    from `project`, so teardown can find everything it created."""

    region: str = "ap-south-1"
    project: str = "biop"

    # Ports
    app_port: int = 8000     # Flask/gunicorn on the instances
    alb_port: int = 80       # public listener
    db_port: int = 5432      # Postgres

    # Compute
    instance_type: str = "t3.micro"
    asg_min: int = 1
    asg_max: int = 3
    asg_desired: int = 2

    # Database (Single-AZ — no standby, so faults are reboot / pool-exhaustion /
    # SG-block, never "failover")
    db_instance_class: str = "db.t3.micro"
    db_engine: str = "postgres"
    db_name: str = "biop"
    db_username: str = "biop"
    db_allocated_storage: int = 20
    db_multi_az: bool = False

    @property
    def tags(self) -> dict:
        return {"Project": self.project, "ManagedBy": f"{self.project}-infra"}

    def name(self, suffix: str) -> str:
        return f"{self.project}-{suffix}"
