"""Low-level S3 operations via boto3."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from config import Config


class S3OperationError(Exception):
    """User-facing S3/API error with safe message."""

    pass


def s3_client(config: Config) -> BaseClient:
    """Create an S3 client from *config*."""
    session = boto3.session.Session(**config.boto_session_kwargs())
    return session.client("s3")


def bucket_exists(client: BaseClient, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            return False
        if code in ("403", "AccessDenied"):
            raise S3OperationError(
                "Access denied when checking the bucket (s3:HeadBucket). "
                "The bucket may still exist; verify IAM permissions."
            ) from e
        raise S3OperationError(f"Could not check bucket: {code or e}") from e


def get_bucket_region(client: BaseClient, bucket: str) -> Optional[str]:
    """Best-effort bucket region from HeadBucket or GetBucketLocation."""
    try:
        r = client.get_bucket_location(Bucket=bucket)
        loc = r.get("LocationConstraint")
        # us-east-1 returns None
        if loc is None:
            return "us-east-1"
        return str(loc)
    except ClientError:
        try:
            r = client.head_bucket(Bucket=bucket)
            hdrs = r.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            rgn = hdrs.get("x-amz-bucket-region")
            return rgn
        except ClientError:
            return None


def create_bucket_if_missing(client: BaseClient, config: Config) -> bool:
    """
    Create the bucket if it does not exist.

    Returns True if created, False if it already existed.
    Raises S3OperationError on failure (including name taken elsewhere).
    """
    if bucket_exists(client, config.s3_bucket_name):
        return False

    region = config.aws_default_region
    params: Dict[str, Any] = {"Bucket": config.s3_bucket_name}
    try:
        if region == "us-east-1":
            client.create_bucket(**params)
        else:
            params["CreateBucketConfiguration"] = {
                "LocationConstraint": region,
            }
            client.create_bucket(**params)
        logging.info("Created bucket %s in region %s", config.s3_bucket_name, region)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        if code == "BucketAlreadyOwnedByYou":
            return False
        if code == "BucketAlreadyExists":
            raise S3OperationError(
                "Bucket name is already taken globally by another account. "
                "Choose a different S3_BUCKET_NAME."
            ) from e
        if code == "OperationAborted":
            raise S3OperationError(msg) from e
        raise S3OperationError(f"Could not create bucket ({code}): {msg}") from e


def head_object_meta(
    client: BaseClient, bucket: str, key: str
) -> Optional[Tuple[int, str]]:
    """
    Return (ContentLength, ETag) if object exists, else None.
    """
    try:
        r = client.head_object(Bucket=bucket, Key=key)
        size = int(r["ContentLength"])
        etag = r.get("ETag") or ""
        return size, etag
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise S3OperationError(
            f"head_object failed for {key!r}: {code}"
        ) from e


def guess_content_type(path: Path) -> Optional[str]:
    """Guess MIME type using stdlib mimetypes."""
    ctype, _ = mimetypes.guess_type(path.name)
    return ctype


def upload_file(
    client: BaseClient,
    config: Config,
    local_path: Path,
    key: str,
    *,
    dry_run: bool,
) -> int:
    """
    Upload *local_path* to *key*. Returns bytes uploaded (0 if dry-run skip).

    Uses SSE-S3 AES256, storage class, and optional ACL from config.
    """
    size = local_path.stat().st_size
    if dry_run:
        logging.info("[dry-run] would upload %s -> s3://%s/%s", local_path, config.s3_bucket_name, key)
        return 0

    extra: Dict[str, Any] = {
        "ServerSideEncryption": "AES256",
        "StorageClass": config.s3_storage_class,
    }
    if config.s3_acl:
        extra["ACL"] = config.s3_acl

    ctype = guess_content_type(local_path)
    if ctype:
        extra["ContentType"] = ctype

    client.upload_file(
        str(local_path),
        config.s3_bucket_name,
        key,
        ExtraArgs=extra,
    )
    return size


def delete_objects_keys(
    client: BaseClient,
    bucket: str,
    keys: List[str],
    *,
    dry_run: bool,
) -> Tuple[int, List[str]]:
    """
    Delete up to 1000 keys per batch. Returns (deleted_count, failures).

    *keys* should be non-empty list of object keys.
    """
    if not keys:
        return 0, []
    if dry_run:
        for k in keys:
            logging.info("[dry-run] would delete s3://%s/%s", bucket, k)
        return len(keys), []

    to_delete = [{"Key": k} for k in keys]
    resp = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": to_delete, "Quiet": True},
    )
    deleted = resp.get("Deleted") or []
    errs = resp.get("Errors") or []
    n = len(deleted)
    failed = [e.get("Key", "?") for e in errs]
    for e in errs:
        logging.error(
            "Delete failed: %s — %s",
            e.get("Key"),
            e.get("Message"),
        )
    return n, failed


def list_all_keys_with_prefix(
    client: BaseClient,
    bucket: str,
    prefix: str,
) -> List[dict]:
    """Return list of object summaries (Key, Size) under *prefix*."""
    out: List[dict] = []
    paginator = client.get_paginator("list_objects_v2")
    kwargs: Dict[str, Any] = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents") or []:
            out.append({"Key": obj["Key"], "Size": obj.get("Size", 0)})
    return out


def delete_all_under_prefix(
    client: BaseClient,
    bucket: str,
    prefix: str,
    *,
    dry_run: bool,
) -> int:
    """Delete every object whose key starts with *prefix*. Returns count deleted."""
    keys = [o["Key"] for o in list_all_keys_with_prefix(client, bucket, prefix)]
    total = 0
    batch_size = 1000
    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        n, _ = delete_objects_keys(client, bucket, batch, dry_run=dry_run)
        total += n
    return total


def delete_bucket_empty(client: BaseClient, bucket: str, *, dry_run: bool) -> None:
    """Delete bucket; must have no objects."""
    if dry_run:
        logging.info("[dry-run] would delete bucket %s", bucket)
        return
    client.delete_bucket(Bucket=bucket)
