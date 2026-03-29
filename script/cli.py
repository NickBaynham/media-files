#!/usr/bin/env python3
"""
Command-line entry point for S3 media management.

Run from the repository root, for example::

    python script/cli.py list-local
    python script/cli.py list-uploaded
    python script/cli.py upload --dry-run
    python script/cli.py upload --public
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

# Support both `python script/cli.py` and `python -m script.cli` from repo root.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from config import ConfigError, load_config, merge_dry_run
from delete_media import (
    run_bucket_exists,
    run_bucket_info,
    run_cleanup,
    run_delete_bucket,
    run_delete_objects,
    run_remove_bucket,
)
from list_media import run_check_public, run_list_local, run_list_uploaded
from s3_utils import S3OperationError
from upload_media import run_upload
from utils import setup_logging


REPO_ROOT = Path(__file__).resolve().parent.parent


def _add_common_s3_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--prefix",
        dest="prefix",
        metavar="PREFIX",
        default=None,
        help="Override S3 key prefix for this run (otherwise from env).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without modifying S3 (overrides DRY_RUN in .env).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Shortcut for debug logging.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-files",
        description=(
            "Manage local media under files/ with an Amazon S3 bucket. "
            "Configure AWS credentials and bucket name via .env (see .env.example)."
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root containing files/ and .env (default: parent of script/).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser(
        "list-local",
        help="List files under files/ with sizes (no AWS calls).",
    )
    p_list.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Shortcut for debug logging.",
    )
    p_list.set_defaults(handler=_cmd_list_local)

    p_list_s3 = sub.add_parser(
        "list-uploaded",
        help="List objects in the S3 bucket (under S3_KEY_PREFIX unless --all-bucket).",
    )
    _add_common_s3_args(p_list_s3)
    p_list_s3.add_argument(
        "--all-bucket",
        action="store_true",
        help=(
            "List every object in the bucket. By default, only keys under the "
            "configured (or --prefix) key prefix are shown."
        ),
    )
    p_list_s3.set_defaults(handler=_cmd_list_uploaded)

    p_chk = sub.add_parser(
        "check-public",
        help=(
            "Send an unauthenticated HTTP HEAD to each object URL to see if "
            "anonymous reads work (bucket policy / ACL effective check)."
        ),
    )
    _add_common_s3_args(p_chk)
    p_chk.add_argument(
        "--all-bucket",
        action="store_true",
        help="Check every object in the bucket (ignore configured key prefix).",
    )
    p_chk.add_argument(
        "--limit",
        type=int,
        default=200,
        metavar="N",
        help="Maximum number of objects to probe (default: 200; use 0 for no limit).",
    )
    p_chk.set_defaults(handler=_cmd_check_public)

    p_up = sub.add_parser(
        "upload",
        help="Upload all files under files/ to S3 (create bucket if missing).",
    )
    _add_common_s3_args(p_up)
    p_up.add_argument(
        "--public",
        action="store_true",
        help=(
            "Public website mode: auto bucket policy (GetObject) when the bucket is new "
            "or has no policy, then upload with public-read ACL or ACL fallback—see README."
        ),
    )
    p_up.add_argument(
        "--no-bucket-policy",
        action="store_true",
        help=(
            "With --public, do not attach or update the automatic anonymous s3:GetObject "
            "bucket policy (you manage policy in the console)."
        ),
    )
    p_up.set_defaults(handler=_cmd_upload)

    p_del_o = sub.add_parser(
        "delete-objects",
        help="Delete S3 objects that match local files under files/.",
    )
    _add_common_s3_args(p_del_o)
    p_del_o.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation before deleting objects.",
    )
    p_del_o.set_defaults(handler=_cmd_delete_objects)

    p_del_b = sub.add_parser(
        "delete-bucket",
        help="Delete the bucket when it is empty (see safety flags).",
    )
    _add_common_s3_args(p_del_b)
    p_del_b.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation before deleting the bucket.",
    )
    p_del_b.add_argument(
        "--empty-under-prefix-first",
        action="store_true",
        help=(
            "Delete all objects under the configured key prefix before "
            "removing the bucket. If any objects remain outside that prefix, "
            "the bucket is not deleted."
        ),
    )
    p_del_b.add_argument(
        "--full-bucket",
        action="store_true",
        help=(
            "With --empty-under-prefix-first, required when S3_KEY_PREFIX is "
            "empty to delete every object in the bucket first (dangerous)."
        ),
    )
    p_del_b.set_defaults(handler=_cmd_delete_bucket)

    p_remove = sub.add_parser(
        "remove-bucket",
        help=(
            "Delete every object in the bucket, then delete the bucket (full reset). "
            "Ignores S3_KEY_PREFIX."
        ),
    )
    p_remove.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation (use only in scripts you trust).",
    )
    p_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without deleting.",
    )
    p_remove.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Shortcut for debug logging.",
    )
    p_remove.set_defaults(handler=_cmd_remove_bucket)

    p_clean = sub.add_parser(
        "cleanup",
        help="Run delete-objects, then optionally delete the bucket.",
    )
    _add_common_s3_args(p_clean)
    p_clean.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmations for delete-objects and bucket deletion.",
    )
    p_clean.add_argument(
        "--delete-bucket",
        action="store_true",
        help="After deleting matching objects, delete the bucket if empty.",
    )
    p_clean.add_argument(
        "--empty-under-prefix-first",
        action="store_true",
        help="When deleting the bucket, empty the key prefix first (see delete-bucket).",
    )
    p_clean.add_argument(
        "--full-bucket",
        action="store_true",
        help="With --empty-under-prefix-first and empty prefix, wipe entire bucket.",
    )
    p_clean.set_defaults(handler=_cmd_cleanup)

    p_info = sub.add_parser(
        "bucket-info",
        help="Show bucket status, region, and object stats.",
    )
    _add_common_s3_args(p_info)
    p_info.set_defaults(handler=_cmd_bucket_info)

    p_ex = sub.add_parser(
        "bucket-exists",
        help="Print 'exists' or 'missing'; exit 0 if the bucket exists, 1 otherwise.",
    )
    _add_common_s3_args(p_ex)
    p_ex.set_defaults(handler=_cmd_bucket_exists)

    return parser


def _effective_log_level(cfg_level: str, verbose: bool) -> str:
    return "DEBUG" if verbose else cfg_level


def _load_cfg(args: argparse.Namespace, *, require_aws: bool):
    cfg = load_config(project_root=args.project_root.resolve(), require_aws=require_aws)
    level = _effective_log_level(cfg.log_level, getattr(args, "verbose", False))
    from dataclasses import replace

    return replace(cfg, log_level=level)


def _cli_dry_run(args: argparse.Namespace) -> Optional[bool]:
    if getattr(args, "dry_run", False):
        return True
    return None


def _cmd_list_local(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=False)
    setup_logging(cfg.log_level)
    return run_list_local(args.project_root.resolve(), config=cfg)


def _cmd_list_uploaded(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    cfg = merge_dry_run(cfg, _cli_dry_run(args))
    setup_logging(cfg.log_level)
    return run_list_uploaded(
        cfg,
        prefix_override=args.prefix,
        all_bucket=bool(getattr(args, "all_bucket", False)),
    )


def _cmd_check_public(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    setup_logging(cfg.log_level)
    return run_check_public(
        cfg,
        prefix_override=args.prefix,
        all_bucket=bool(getattr(args, "all_bucket", False)),
        limit=int(args.limit),
    )


def _cmd_upload(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    return run_upload(
        cfg,
        prefix_override=args.prefix,
        cli_dry_run=_cli_dry_run(args),
        public_read=bool(getattr(args, "public", False)),
        skip_public_bucket_policy=bool(getattr(args, "no_bucket_policy", False)),
    )


def _cmd_delete_objects(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    return run_delete_objects(
        cfg,
        prefix_override=args.prefix,
        force=args.force,
        cli_dry_run=_cli_dry_run(args),
    )


def _cmd_delete_bucket(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    return run_delete_bucket(
        cfg,
        prefix_override=args.prefix,
        force=args.force,
        empty_under_prefix_first=args.empty_under_prefix_first,
        full_bucket=args.full_bucket,
        cli_dry_run=_cli_dry_run(args),
    )


def _cmd_remove_bucket(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    return run_remove_bucket(
        cfg,
        force=args.force,
        cli_dry_run=_cli_dry_run(args),
    )


def _cmd_cleanup(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    return run_cleanup(
        cfg,
        prefix_override=args.prefix,
        force=args.force,
        delete_bucket=args.delete_bucket,
        empty_under_prefix_first=args.empty_under_prefix_first,
        full_bucket=args.full_bucket,
        cli_dry_run=_cli_dry_run(args),
    )


def _cmd_bucket_info(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    cfg = merge_dry_run(cfg, _cli_dry_run(args))
    setup_logging(cfg.log_level)
    return run_bucket_info(cfg, prefix_override=args.prefix)


def _cmd_bucket_exists(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args, require_aws=True)
    cfg = merge_dry_run(cfg, _cli_dry_run(args))
    setup_logging(cfg.log_level)
    return run_bucket_exists(cfg)


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return int(handler(args))
    except ConfigError as e:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        logging.error("%s", e)
        return 2
    except S3OperationError as e:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        logging.error("%s", e)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
