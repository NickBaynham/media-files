"""List local media files under files/ and objects in S3."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from botocore.exceptions import ClientError
from tabulate import tabulate

from config import Config, load_config
from s3_utils import S3OperationError, bucket_exists, list_all_keys_with_prefix, s3_client
from utils import (
    anonymous_http_head_status,
    format_bytes,
    s3_public_object_url,
    scan_files_directory,
    setup_logging,
)


def _s3_list_prefix(key_prefix: str) -> str:
    """Prefix passed to ListObjectsV2 (matches upload/delete \"under prefix\" semantics)."""
    if not key_prefix:
        return ""
    return key_prefix if key_prefix.endswith("/") else f"{key_prefix}/"


def run_list_local(
    project_root: Optional[Path] = None,
    *,
    config: Optional[Config] = None,
) -> int:
    """
    Print a table of files under ``files/`` with sizes and totals.

    Does not require AWS credentials.
    """
    root = (project_root or Path.cwd()).resolve()
    cfg = config if config is not None else load_config(project_root=root, require_aws=False)
    setup_logging(cfg.log_level)

    files_dir = cfg.files_dir()
    if not files_dir.is_dir():
        print(
            f"No local media directory at {files_dir}. "
            "Create `files/` and add assets, or run from the project root.",
            file=sys.stderr,
        )
        return 0

    entries, total_bytes = scan_files_directory(files_dir)
    if not entries:
        print(f"Directory exists but is empty: {files_dir}")
        return 0

    rows = []
    for e in entries:
        rows.append(
            [
                e.relative_posix,
                e.size_bytes,
                format_bytes(e.size_bytes),
            ]
        )
    rows.append(["— TOTAL —", total_bytes, format_bytes(total_bytes)])

    print(
        tabulate(
            rows,
            headers=["Path (under files/)", "Bytes", "Size"],
            tablefmt="github",
        )
    )
    print(f"\nFiles: {len(entries)}")
    logging.debug("Total bytes: %s", total_bytes)
    return 0


def run_list_uploaded(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    all_bucket: bool = False,
) -> int:
    """
    Print a table of object keys in the configured bucket (under the key prefix
    unless *all_bucket* is True).
    """
    setup_logging(config.log_level)

    kp = config.effective_key_prefix(prefix_override)
    list_prefix = "" if all_bucket else _s3_list_prefix(kp)

    client = s3_client(config)
    if not bucket_exists(client, config.s3_bucket_name):
        logging.error("Bucket does not exist: %s", config.s3_bucket_name)
        return 1

    try:
        objs = list_all_keys_with_prefix(client, config.s3_bucket_name, list_prefix)
    except S3OperationError as e:
        logging.error("%s", e)
        return 1
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logging.error("ListObjects failed: %s", code or e)
        return 1

    objs.sort(key=lambda o: o["Key"])
    if not objs:
        if all_bucket or not kp:
            where = "in this bucket"
        else:
            where = f"under prefix {list_prefix!r}"
        print(f"No objects {where} in s3://{config.s3_bucket_name}")
        return 0

    rows = []
    total_bytes = 0
    for o in objs:
        sz = int(o.get("Size", 0))
        total_bytes += sz
        rows.append([o["Key"], sz, format_bytes(sz)])
    rows.append(["— TOTAL —", total_bytes, format_bytes(total_bytes)])

    print(f"s3://{config.s3_bucket_name}/")
    if all_bucket and kp:
        print("(listing entire bucket; ignoring configured key prefix for this run)")
    elif list_prefix:
        print(f"(listing prefix: {list_prefix!r})")
    else:
        print("(listing entire bucket)")
    print()
    print(
        tabulate(
            rows,
            headers=["Object key", "Bytes", "Size"],
            tablefmt="github",
        )
    )
    print(f"\nObjects: {len(objs)}")
    logging.debug("Total bytes: %s", total_bytes)
    return 0


def run_check_public(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    all_bucket: bool = False,
    limit: int = 200,
) -> int:
    """
    For each listed object, send an unauthenticated HTTP HEAD to its public URL.

    **200** means anonymous read likely works; **403** usually means private or
    Block Public Access / no bucket policy. Uses **AWS_DEFAULT_REGION** for the
    hostname (must match the bucket region for a meaningful check).
    """
    setup_logging(config.log_level)

    kp = config.effective_key_prefix(prefix_override)
    list_prefix = "" if all_bucket else _s3_list_prefix(kp)

    client = s3_client(config)
    if not bucket_exists(client, config.s3_bucket_name):
        logging.error("Bucket does not exist: %s", config.s3_bucket_name)
        return 1

    try:
        objs = list_all_keys_with_prefix(client, config.s3_bucket_name, list_prefix)
    except S3OperationError as e:
        logging.error("%s", e)
        return 1
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logging.error("ListObjects failed: %s", code or e)
        return 1

    if not objs:
        print("No objects to check.")
        return 0

    objs.sort(key=lambda o: o["Key"])
    if limit > 0:
        objs = objs[:limit]

    region = config.aws_default_region
    bucket = config.s3_bucket_name
    rows = []
    public_n = 0
    for o in objs:
        key = o["Key"]
        url = s3_public_object_url(bucket, region, key)
        code, err = anonymous_http_head_status(url)
        if code == 200:
            public_n += 1
            label = "yes"
        elif code == 403:
            label = "no (forbidden)"
        elif code == 404:
            label = "no (not found)"
        elif code is None:
            label = f"error ({err or 'network'})"
        else:
            label = f"no (HTTP {code})"
        rows.append([key, code if code is not None else "—", label])

    print(
        tabulate(
            rows,
            headers=["Object key", "Anonymous HEAD", "Public read?"],
            tablefmt="github",
        )
    )
    print()
    print(f"Checked: {len(rows)} object(s) (unauthenticated HEAD, region={region!r}).")
    print(f"Likely public (200): {public_n}")
    if public_n < len(rows):
        print(
            "Tip: 403 usually means add a bucket policy for s3:GetObject or adjust "
            "Block Public Access (see README)."
        )
    return 0
