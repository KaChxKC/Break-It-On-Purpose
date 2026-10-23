"""Ship run manifests and their metric time-series to S3.

The S3 bucket is the durable home of the labeled dataset and is intentionally
left untouched by `infra.manage down`, so tearing down the compute never
destroys collected data.
"""

import json
import os

from infra.aws import Aws


def data_bucket_name(aws: Aws) -> str:
    account = aws.client("sts").get_caller_identity()["Account"]
    return f"{aws.config.project}-data-{account}"


def ensure_data_bucket(aws: Aws) -> str:
    s3 = aws.client("s3")
    bucket = data_bucket_name(aws)
    existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    if bucket not in existing:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": aws.config.region},
        )
    return bucket


def upload_run(aws: Aws, bucket: str, manifest: dict,
               metric_files: list[str] | None = None) -> str:
    s3 = aws.client("s3")
    run_id = manifest["run_id"]
    s3.put_object(
        Bucket=bucket,
        Key=f"runs/{run_id}/manifest.json",
        Body=json.dumps(manifest, indent=2).encode(),
    )
    for path in metric_files or []:
        s3.upload_file(path, bucket, f"runs/{run_id}/metrics/{os.path.basename(path)}")
    return f"s3://{bucket}/runs/{run_id}/"


def list_runs(aws: Aws, bucket: str) -> list[str]:
    resp = aws.client("s3").list_objects_v2(Bucket=bucket, Prefix="runs/", Delimiter="/")
    return [p["Prefix"].split("/")[1] for p in resp.get("CommonPrefixes", [])]
