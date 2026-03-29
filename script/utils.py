"""Shared helpers: paths, sizes, logging, prompts."""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Tuple


class LocalFileEntry(NamedTuple):
    """A file under the local media directory."""

    relative_posix: str
    path: Path
    size_bytes: int


def setup_logging(level_name: str) -> None:
    """Configure root logging for CLI (idempotent enough for our use)."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def redact_env_value(key: str, value: str) -> str:
    """Avoid printing secrets when debugging."""
    upper = key.upper()
    if any(
        x in upper
        for x in ("SECRET", "KEY", "TOKEN", "PASSWORD", "CREDENTIAL")
    ):
        if not value:
            return ""
        return "***" if len(value) < 8 else f"{value[:2]}...{value[-2:]}"
    return value


def format_bytes(n: int) -> str:
    """Human-readable size: bytes, KB, MB, GB as appropriate."""
    if n < 0:
        n = 0
    if n < 1024:
        return f"{n} B"
    kb = n / 1024.0
    if kb < 1024:
        return f"{kb:.2f} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.2f} MB"
    gb = mb / 1024.0
    return f"{gb:.2f} GB"


def scan_files_directory(
    files_dir: Path,
    *,
    follow_symlinks: bool = False,
) -> Tuple[List[LocalFileEntry], int]:
    """
    Recursively list files under *files_dir*.

    Returns (entries sorted by relative path, total_bytes).
    Skips directories. Missing *files_dir* returns ([], 0).
    """
    if not files_dir.is_dir():
        return [], 0

    entries: List[LocalFileEntry] = []
    total = 0
    base = files_dir.resolve()

    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if p.is_symlink() and not follow_symlinks:
            continue
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        rel_str = rel.as_posix()
        try:
            size = p.stat().st_size
        except OSError:
            logging.warning("Could not stat file, skipping: %s", rel_str)
            continue
        entries.append(LocalFileEntry(relative_posix=rel_str, path=p, size_bytes=size))
        total += size

    return entries, total


def to_object_key(key_prefix: str, relative_posix: str) -> str:
    """Build S3 object key from prefix and relative path (forward slashes)."""
    rel = relative_posix.replace("\\", "/").lstrip("/")
    if not key_prefix:
        return rel
    return f"{key_prefix}/{rel}" if rel else key_prefix


def md5_hex_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """MD5 hex digest of file contents (for ETag comparison on single-part uploads)."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_etag_for_compare(etag: str) -> Optional[str]:
    """
    Return hex MD5 from ETag if it is a single-part MD5, else None.

    Multipart ETags look like ``deadbeef-5`` (hyphen + part count).
    """
    if not etag:
        return None
    e = etag.strip().strip('"')
    if "-" in e:
        return None
    if len(e) == 32 and all(c in "0123456789abcdef" for c in e.lower()):
        return e.lower()
    return None


def confirm_yes_no(message: str, *, default_no: bool = True) -> bool:
    """Prompt on stderr; return True only for yes/y."""
    choices = "[y/N]" if default_no else "[Y/n]"
    try:
        raw = input(f"{message} {choices} ").strip().lower()
    except EOFError:
        return False
    if not raw:
        return not default_no
    return raw in ("y", "yes")


def chunked(iterable: List[str], size: int) -> Iterator[List[str]]:
    """Yield slices of *iterable* of length at most *size*."""
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]
