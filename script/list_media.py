"""List local media files under files/."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from tabulate import tabulate

from config import Config, load_config
from utils import format_bytes, scan_files_directory, setup_logging


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
