"""Upload local media to S3."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from botocore.exceptions import ClientError

from config import Config, merge_dry_run
from s3_utils import (
    S3OperationError,
    bucket_has_policy,
    bucket_policy_resource_arn,
    create_bucket_if_missing,
    ensure_public_get_object_bucket_policy,
    get_bucket_region,
    head_object_meta,
    s3_client,
    upload_file,
)
from utils import (
    LocalFileEntry,
    md5_hex_file,
    parse_etag_for_compare,
    probe_public_read_for_keys,
    scan_files_directory,
    setup_logging,
    to_object_key,
)


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
    public_read: bool = False,
    skip_public_bucket_policy: bool = False,
) -> int:
    """
    Ensure bucket exists, then upload all files under ``files/``.

    If *public_read* is True, each object is uploaded with the ``public-read`` canned
    ACL when the bucket allows ACLs. If the bucket has ACLs disabled (common with
    **Bucket owner enforced**), the same bytes are uploaded **without** an ACL and
    the summary tells you to add a **bucket policy** for anonymous ``GetObject``.

    When the bucket was **just created** or has **no bucket policy yet**, this command
    also attaches an anonymous **s3:GetObject** policy (unless *skip_public_bucket_policy*).

    Skip-if-unchanged is disabled so every file is uploaded.

    Returns process exit code (0 on success).
    """
    config = merge_dry_run(config, cli_dry_run)
    setup_logging(config.log_level)

    upload_cfg = replace(config, s3_acl="public-read") if public_read else config
    if public_read:
        logging.info(
            "Public upload mode: applies anonymous s3:GetObject bucket policy when the "
            "bucket is new or has no policy (--no-bucket-policy to skip); tries "
            "public-read ACL on each object; falls back without ACL if needed."
        )

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
    public_bucket_policy_applied = False
    try:
        created = create_bucket_if_missing(client, config)
        if created:
            logging.info("Bucket is ready.")

        if public_read and not skip_public_bucket_policy:
            has_pol = bucket_has_policy(client, config.s3_bucket_name)
            if has_pol is None:
                logging.warning(
                    "Cannot read bucket policy (access denied); skipping automatic "
                    "public GetObject policy. Grant s3:GetBucketPolicy or add a policy in the console."
                )
            elif created or not has_pol:
                public_bucket_policy_applied = ensure_public_get_object_bucket_policy(
                    client,
                    config.s3_bucket_name,
                    key_prefix,
                    dry_run=config.dry_run,
                )
            else:
                logging.info(
                    "Bucket already has a bucket policy; not replacing it. "
                    "Add s3:GetObject for your prefix if public URLs still return 403."
                )
    except S3OperationError as e:
        logging.error("%s", e)
        return 1

    uploaded = 0
    skipped = 0
    failed = 0
    bytes_uploaded = 0
    acl_fallback_count = 0
    uploaded_keys: List[str] = []

    for entry in entries:
        key = to_object_key(key_prefix, entry.relative_posix)
        try:
            if not public_read:
                meta = head_object_meta(client, config.s3_bucket_name, key)
                if meta:
                    rsize, etag = meta
                    if should_skip_upload(entry, rsize, etag):
                        skipped += 1
                        logging.debug("Skip unchanged: %s", key)
                        continue
            _, used_acl_fallback = upload_file(
                client,
                upload_cfg,
                entry.path,
                key,
                dry_run=config.dry_run,
                fallback_without_acl=public_read,
            )
            if used_acl_fallback:
                acl_fallback_count += 1
            if public_read:
                uploaded_keys.append(key)
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
    if public_read:
        if public_bucket_policy_applied and not config.dry_run:
            scope = bucket_policy_resource_arn(config.s3_bucket_name, key_prefix)
            print(f"  Bucket policy: anonymous s3:GetObject configured for {scope!r}.")
        if acl_fallback_count:
            print(
                f"  Public via object ACL: no (bucket disallows ACLs; "
                f"{acl_fallback_count} object(s) uploaded without canned ACL)"
            )
            if not public_bucket_policy_applied:
                print(
                    "  Next step: add a bucket policy allowing s3:GetObject for your keys "
                    "(see README example) so the web / Amplify can read them."
                )
        elif not acl_fallback_count:
            print("  Object ACL: public-read (anonymous read, if Block Public Access allows)")

    if public_read and uploaded_keys:
        if config.dry_run:
            print()
            print("Public access validation — skipped (dry-run).")
            logging.info("Public access validation skipped (dry-run).")
        else:
            probe_region = get_bucket_region(client, config.s3_bucket_name) or config.aws_default_region
            ok_n, failures = probe_public_read_for_keys(
                config.s3_bucket_name,
                probe_region,
                uploaded_keys,
            )
            print()
            print("Public access validation (anonymous HEAD)")
            print("--------------------------------------------")
            if not failures:
                print(
                    f"  Validated — all {ok_n} uploaded object(s) returned HTTP 200 "
                    f"(publicly reachable via unauthenticated request)."
                )
                logging.info(
                    "Public access validated: all %s object(s) returned HTTP 200 (anonymous HEAD, region=%s).",
                    ok_n,
                    probe_region,
                )
            else:
                print(
                    f"  Not validated — {len(failures)} of {len(uploaded_keys)} object(s) "
                    "did not return HTTP 200."
                )
                print(
                    "  Browsers and Amplify may still be blocked; add a bucket policy for "
                    "s3:GetObject or adjust Block Public Access (see README)."
                )
                logging.warning(
                    "Public access check failed: %s of %s object(s) not anonymously readable (HEAD != 200).",
                    len(failures),
                    len(uploaded_keys),
                )
                for k, code in failures[:10]:
                    status = str(code) if code is not None else "error/network"
                    logging.warning("  %s → HTTP %s", k, status)
                    print(f"    {k!r} → {status}")
                if len(failures) > 10:
                    extra = len(failures) - 10
                    print(f"    ... and {extra} more")
                    logging.warning("  ... and %s more keys with failed HEAD", extra)

    if config.dry_run:
        print("  (dry-run mode; no objects were written)")
    return 0 if failed == 0 else 1
