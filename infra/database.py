import secrets

from botocore.exceptions import ClientError

from .aws import Aws, tag_list
from .network import get_default_subnets, get_default_vpc

# App and instances read the master password from this SSM parameter, so it is
# never hardcoded and never printed.
DB_PASSWORD_PARAM = "/{project}/db/password"


def password_param_name(cfg) -> str:
    return DB_PASSWORD_PARAM.format(project=cfg.project)


def _find_db(rds, db_id: str):
    try:
        return rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "DBInstanceNotFound":
            return None
        raise


def _ensure_password(ssm, cfg) -> str:
    name = password_param_name(cfg)
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ParameterNotFound":
            raise
    # token_urlsafe avoids characters RDS rejects in a master password
    password = secrets.token_urlsafe(24)
    ssm.put_parameter(Name=name, Value=password, Type="SecureString",
                      Tags=tag_list(cfg.tags))
    return password


def _ensure_subnet_group(rds, cfg, subnet_ids: list[str]) -> str:
    name = cfg.name("db-subnets")
    try:
        rds.describe_db_subnet_groups(DBSubnetGroupName=name)
        return name
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "DBSubnetGroupNotFoundFault":
            raise
    rds.create_db_subnet_group(
        DBSubnetGroupName=name,
        DBSubnetGroupDescription=f"{cfg.project} db subnets",
        SubnetIds=subnet_ids,
        Tags=tag_list({**cfg.tags, "Name": name}),
    )
    return name


def ensure_database(aws: Aws, rds_sg_id: str, wait: bool = False) -> dict:
    """Create (or reuse) the Single-AZ Postgres instance, reachable only via the
    rds security group. Returns the RDS instance description."""
    cfg = aws.config
    ec2, rds, ssm = aws.client("ec2"), aws.client("rds"), aws.client("ssm")

    vpc_id = get_default_vpc(ec2)
    subnet_group = _ensure_subnet_group(rds, cfg, get_default_subnets(ec2, vpc_id))

    db_id = cfg.name("db")
    existing = _find_db(rds, db_id)
    if existing:
        return existing

    rds.create_db_instance(
        DBInstanceIdentifier=db_id,
        DBInstanceClass=cfg.db_instance_class,
        Engine=cfg.db_engine,
        AllocatedStorage=cfg.db_allocated_storage,
        StorageType="gp3",
        DBName=cfg.db_name,
        MasterUsername=cfg.db_username,
        MasterUserPassword=_ensure_password(ssm, cfg),
        MultiAZ=cfg.db_multi_az,          # False -> Single-AZ (no standby)
        PubliclyAccessible=False,
        VpcSecurityGroupIds=[rds_sg_id],
        DBSubnetGroupName=subnet_group,
        BackupRetentionPeriod=0,          # no automated backups for the lab (cost)
        Tags=tag_list({**cfg.tags, "Name": db_id}),
    )
    if wait:
        rds.get_waiter("db_instance_available").wait(DBInstanceIdentifier=db_id)
    return _find_db(rds, db_id)


def delete_database(aws: Aws, wait: bool = False) -> None:
    cfg = aws.config
    rds, ssm = aws.client("rds"), aws.client("ssm")
    db_id = cfg.name("db")

    if _find_db(rds, db_id):
        rds.delete_db_instance(
            DBInstanceIdentifier=db_id,
            SkipFinalSnapshot=True,        # no snapshot storage left billing after teardown
            DeleteAutomatedBackups=True,
        )
        if wait:
            rds.get_waiter("db_instance_deleted").wait(DBInstanceIdentifier=db_id)

    # Subnet group can only be removed once the instance is gone.
    try:
        rds.delete_db_subnet_group(DBSubnetGroupName=cfg.name("db-subnets"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("DBSubnetGroupNotFoundFault",
                                                 "InvalidDBSubnetGroupStateFault"):
            raise

    try:
        ssm.delete_parameter(Name=password_param_name(cfg))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ParameterNotFound":
            raise
