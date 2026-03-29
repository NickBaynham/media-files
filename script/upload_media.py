"""Upload local media to S3."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from botocore.exceptions import ClientError

from config import Config, merge_dry_run
from s3_utils import S3OperationError, create_bucket_if_missing, head_object_meta, s3_client, upload_file
from utils import LocalFileEntry, md5_hex_file, parse_etag_for_compare, scan_files_directory, setup_logging, to_object_key


def should_skip_upload(
    local: LocalFileEntry,
    remote_size: int,
    remote_etag: str,
) -> bool:
    """
    Return True if remote object likely matches local file.

    Uses size first, then MD5 ETag when the ETag is a single-part hex digest.
    If ETag is multipart-style, same size is not enough — re-upload.
    """
    if local.size_bytes != remote_size:
        return False
    parsed = parse_etag_for_compare(remote_etag)
    if parsed is None:
        logging.debug(
            "Multipart or non-MD5 ETag for %s; re-uploading to be safe.",
            local.relative_posix,
        )
        return False
    try:
        local_md5 = md5_hex_file(local.path)
    except OSError:
        return False
    return local_md5.lower() == parsed


def run_upload(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    cli_dry_run: Optional[bool] = None,
) -> int:
    """
    Ensure bucket exists, then upload all files under ``files/``.

    Returns process exit code (0 on success).
    """
    config = merge_dry_run(config, cli_dry_run)
    setup_logging(config.log_level)

    files_dir = config.files_dir()
    if not files_dir.is_dir():
        logging.error("Local directory does not exist: %s", files_dir)
        return 1

    entries, _ = scan_files_directory(files_dir)
    if not entries:
        logging.warning("No files found under %s; nothing to upload.", files_dir)
        return 0

    key_prefix = config.effective_key_prefix(prefix_override)

    client = s3_client(config)
    try:
        created = create_bucket_if_missing(client, config)
        if created:
            logging.info("Bucket is ready.")
    except S3OperationError as e:
        logging.error("%s", e)
        return 1

    uploaded = 0
    skipped = 0
    failed = 0
    bytes_uploaded = 0

    for entry in entries:
        key = to_object_key(key_prefix, entry.relative_posix)
        try:
            meta = head_object_meta(client, config.s3_bucket_name, key)
            if meta:
                rsize, etag = meta
                if should_skip_upload(entry, rsize, etag):
                    skipped += 1
                    logging.debug("Skip unchanged: %s", key)
                    continue
            upload_file(
                client,
                config,
                entry.path,
                key,
                dry_run=config.dry_run,
            )
            if not config.dry_run:
                uploaded += 1
                bytes_uploaded += entry.size_bytes
            else:
                uploaded += 1
                bytes_uploaded += entry.size_bytes
        except S3OperationError as e:
            logging.error("%s", e)
            failed += 1
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            logging.error("Upload failed for %s: %s", key, code or e)
            failed += 1
        except OSError as e:
            logging.error("Local read error for %s: %s", entry.relative_posix, e)
            failed += 1

    print()
    print("Upload summary")
    print("--------------")
    print(f"  Uploaded: {uploaded}")
    print(f"  Skipped (unchanged): {skipped}")
    print(f"  Failed: {failed}")
    print(f"  Bytes uploaded: {bytes_uploaded}")
    if config.dry_run:
        print("  (dry-run mode; no objects were written)")
    return 0 if failed == 0 else 1
