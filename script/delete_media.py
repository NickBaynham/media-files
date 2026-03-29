"""Delete S3 objects and buckets (prefix-safe)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from botocore.exceptions import ClientError

from config import Config, merge_dry_run
from s3_utils import (
    bucket_exists,
    delete_all_under_prefix,
    delete_bucket_empty,
    delete_objects_keys,
    get_bucket_region,
    list_all_keys_with_prefix,
    s3_client,
)
from utils import confirm_yes_no, scan_files_directory, setup_logging, to_object_key, chunked


def _list_prefix_for_config(key_prefix: str) -> str:
    """S3 ListObjectsV2 prefix for \"everything under this logical folder\"."""
    if not key_prefix:
        return ""
    return key_prefix if key_prefix.endswith("/") else f"{key_prefix}/"


def keys_for_local_files(config: Config, prefix_override: Optional[str]) -> List[str]:
    """Object keys corresponding to each file under ``files/``."""
    files_dir = config.files_dir()
    entries, _ = scan_files_directory(files_dir)
    kp = config.effective_key_prefix(prefix_override)
    return [to_object_key(kp, e.relative_posix) for e in entries]


def run_delete_objects(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    force: bool = False,
    cli_dry_run: Optional[bool] = None,
) -> int:
    """
    Delete objects in S3 that match local files under ``files/``.

    Confirms unless *force* is True.
    """
    config = merge_dry_run(config, cli_dry_run)
    setup_logging(config.log_level)

    files_dir = config.files_dir()
    if not files_dir.is_dir():
        logging.error("Local directory does not exist: %s", files_dir)
        return 1

    keys = keys_for_local_files(config, prefix_override)
    if not keys:
        logging.warning("No local files mapped; nothing to delete in S3.")
        return 0

    client = s3_client(config)
    if not bucket_exists(client, config.s3_bucket_name):
        logging.error("Bucket does not exist: %s", config.s3_bucket_name)
        return 1

    preview = keys[:10]
    more = len(keys) - len(preview)
    print("The following keys will be removed (under your configured prefix only):")
    for k in preview:
        print(f"  s3://{config.s3_bucket_name}/{k}")
    if more > 0:
        print(f"  ... and {more} more")
    if config.dry_run:
        print("(dry-run: no deletes will be performed)")

    if not force and not config.dry_run:
        if not confirm_yes_no("Proceed with deletion?", default_no=True):
            print("Aborted.")
            return 0

    failed = 0
    deleted = 0
    for batch in chunked(keys, 1000):
        try:
            n, fails = delete_objects_keys(
                client,
                config.s3_bucket_name,
                batch,
                dry_run=config.dry_run,
            )
            deleted += n
            failed += len(fails)
        except ClientError as e:
            logging.error("Batch delete failed: %s", e)
            failed += len(batch)

    print()
    print(f"Deleted (or dry-run): {deleted}  Failed: {failed}")
    return 0 if failed == 0 else 1


def run_remove_bucket(
    config: Config,
    *,
    force: bool = False,
    cli_dry_run: Optional[bool] = None,
) -> int:
    """
    Delete every object in ``S3_BUCKET_NAME``, then delete the bucket.

    Ignores ``S3_KEY_PREFIX`` and any CLI ``--prefix`` — use this for a full reset
    (for example before recreating the bucket with different public-access settings).
    """
    config = merge_dry_run(config, cli_dry_run)
    setup_logging(config.log_level)

    client = s3_client(config)
    name = config.s3_bucket_name
    if not bucket_exists(client, name):
        logging.error("Bucket does not exist: %s", name)
        return 1

    all_objs = list_all_keys_with_prefix(client, name, "")
    n_obj = len(all_objs)
    total_sz = sum(int(o.get("Size", 0)) for o in all_objs)

    print()
    print("REMOVE BUCKET (full reset)")
    print("===========================")
    print(f"  Bucket:   s3://{name}")
    print(f"  Objects:  {n_obj}")
    print(f"  Bytes:    {total_sz}")
    print()
    print("  This removes EVERY object (all prefixes), then deletes the bucket.")
    print("  It does not use S3_KEY_PREFIX.")
    if config.dry_run:
        print("  (dry-run: no changes will be made)")

    if not force and not config.dry_run:
        if n_obj:
            prompt = f"Delete all {n_obj} object(s) and bucket {name!r}?"
        else:
            prompt = f"Delete empty bucket {name!r}?"
        if not confirm_yes_no(prompt, default_no=True):
            print("Aborted.")
            return 0

    try:
        if n_obj > 0:
            logging.info("Deleting all objects in bucket %s ...", name)
            delete_all_under_prefix(client, name, "", dry_run=config.dry_run)
            if not config.dry_run:
                remaining = list_all_keys_with_prefix(client, name, "")
                if remaining:
                    logging.error(
                        "%s object(s) still remain; bucket not deleted.",
                        len(remaining),
                    )
                    return 1
        delete_bucket_empty(client, name, dry_run=config.dry_run)
    except ClientError as e:
        logging.error("%s", e)
        return 1

    if config.dry_run:
        logging.info("[dry-run] remove-bucket simulation complete")
    else:
        logging.info("Bucket removed: %s", name)
        print()
        print(f"Done. Bucket {name!r} no longer exists. Run `make upload` to create it again.")
    return 0


def run_delete_bucket(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    force: bool = False,
    empty_under_prefix_first: bool = False,
    full_bucket: bool = False,
    cli_dry_run: Optional[bool] = None,
) -> int:
    """
    Delete the S3 bucket when it is empty.

    If *empty_under_prefix_first*, delete all objects under the configured key
    prefix first. If the key prefix is empty, *full_bucket* must be True to
    delete every object in the bucket (explicit opt-in).
    """
    config = merge_dry_run(config, cli_dry_run)
    setup_logging(config.log_level)

    client = s3_client(config)
    if not bucket_exists(client, config.s3_bucket_name):
        logging.error("Bucket does not exist: %s", config.s3_bucket_name)
        return 1

    key_prefix = config.effective_key_prefix(prefix_override)
    all_objs = list_all_keys_with_prefix(client, config.s3_bucket_name, "")

    if all_objs and not empty_under_prefix_first:
        logging.error(
            "Bucket is not empty (%s object(s)). Refusing to delete.\n"
            "Run `delete-objects` or `cleanup`, or pass --empty-under-prefix-first "
            "(see help for full-bucket rules).",
            len(all_objs),
        )
        return 1

    if empty_under_prefix_first:
        if key_prefix:
            lp = _list_prefix_for_config(key_prefix)
            logging.info("Deleting all objects under prefix %r ...", lp)
            try:
                delete_all_under_prefix(
                    client,
                    config.s3_bucket_name,
                    lp,
                    dry_run=config.dry_run,
                )
            except ClientError as e:
                logging.error("%s", e)
                return 1
        else:
            if not full_bucket:
                logging.error(
                    "S3_KEY_PREFIX is empty. Refusing to wipe entire bucket without "
                    "--full-bucket (in addition to --empty-under-prefix-first)."
                )
                return 1
            logging.warning("Deleting ALL objects in bucket %s", config.s3_bucket_name)
            try:
                delete_all_under_prefix(
                    client,
                    config.s3_bucket_name,
                    "",
                    dry_run=config.dry_run,
                )
            except ClientError as e:
                logging.error("%s", e)
                return 1

        # Refresh listing
        all_objs = list_all_keys_with_prefix(client, config.s3_bucket_name, "")
        if all_objs:
            logging.error(
                "After prefix cleanup, %s object(s) remain (outside your prefix). "
                "Bucket was NOT deleted.",
                len(all_objs),
            )
            return 1

    if not force and not config.dry_run:
        print(
            f"This will permanently delete the empty bucket: {config.s3_bucket_name}",
        )
        if not confirm_yes_no("Type yes to confirm bucket deletion", default_no=True):
            print("Aborted.")
            return 0

    try:
        delete_bucket_empty(client, config.s3_bucket_name, dry_run=config.dry_run)
        if not config.dry_run:
            logging.info("Bucket deleted: %s", config.s3_bucket_name)
        else:
            logging.info("[dry-run] bucket delete skipped after simulation")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "BucketNotEmpty":
            logging.error("Bucket still not empty; cannot delete.")
            return 1
        logging.error("%s", e)
        return 1
    return 0


def run_cleanup(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
    force: bool = False,
    delete_bucket: bool = False,
    empty_under_prefix_first: bool = False,
    full_bucket: bool = False,
    cli_dry_run: Optional[bool] = None,
) -> int:
    """Delete objects for local files, then optionally delete the bucket."""
    code = run_delete_objects(
        config,
        prefix_override=prefix_override,
        force=force,
        cli_dry_run=cli_dry_run,
    )
    if code != 0:
        return code
    if not delete_bucket:
        return 0
    print()
    print("--- Bucket deletion phase ---")
    return run_delete_bucket(
        config,
        prefix_override=prefix_override,
        force=force,
        empty_under_prefix_first=empty_under_prefix_first,
        full_bucket=full_bucket,
        cli_dry_run=cli_dry_run,
    )


def run_bucket_info(
    config: Config,
    *,
    prefix_override: Optional[str] = None,
) -> int:
    """Print bucket existence, region, size stats, and effective prefixes."""
    setup_logging(config.log_level)
    client = s3_client(config)
    name = config.s3_bucket_name
    kp = config.effective_key_prefix(prefix_override)

    exists = bucket_exists(client, name)
    print(f"Bucket: {name}")
    print(f"  Exists: {exists}")
    if not exists:
        print(f"  Configured key prefix: {kp or '(none)'}")
        return 0

    region = get_bucket_region(client, name)
    print(f"  Region (reported): {region or 'unknown'}")

    objs_all = list_all_keys_with_prefix(client, name, "")
    total_count = len(objs_all)
    total_size = sum(o.get("Size", 0) for o in objs_all)
    lp = _list_prefix_for_config(kp)
    if lp:
        under = [o for o in objs_all if o["Key"].startswith(lp) or o["Key"] == kp]
    else:
        under = objs_all
    under_size = sum(o.get("Size", 0) for o in under)

    print(f"  Object count (whole bucket): {total_count}")
    print(f"  Total size (whole bucket): {total_size} bytes")
    print(f"  Configured key prefix: {kp or '(none)'}")
    if kp:
        print(f"  Objects under prefix (approx): {len(under)}")
        print(f"  Size under prefix (approx): {under_size} bytes")
    return 0


def run_bucket_exists(config: Config) -> int:
    """Exit 0 if bucket exists, 1 otherwise."""
    setup_logging(config.log_level)
    client = s3_client(config)
    ok = bucket_exists(client, config.s3_bucket_name)
    print("exists" if ok else "missing")
    return 0 if ok else 1
