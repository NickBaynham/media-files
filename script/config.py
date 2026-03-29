"""Load and validate configuration from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""

    pass


# S3 bucket naming rules (simplified check; see AWS docs for full rules).
_BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def _looks_like_ipv4_address(name: str) -> bool:
    """True if *name* looks like an IPv4 address (disallowed for S3 bucket names)."""
    parts = name.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    return all(0 <= n <= 255 for n in nums)


def _normalize_key_prefix(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = raw.strip().replace("\\", "/")
    while s.startswith("/"):
        s = s[1:]
    if s.endswith("/"):
        s = s[:-1]
    return s


def validate_bucket_name(name: str) -> None:
    """Raise ConfigError if *name* is not a plausible S3 bucket name."""
    if not name or len(name) < 3 or len(name) > 63:
        raise ConfigError("S3_BUCKET_NAME must be between 3 and 63 characters.")
    if name != name.lower():
        raise ConfigError("S3_BUCKET_NAME must be lowercase.")
    if ".." in name:
        raise ConfigError("S3_BUCKET_NAME must not contain adjacent periods (..).")
    if not _BUCKET_NAME_RE.match(name):
        raise ConfigError(
            "S3_BUCKET_NAME must start and end with a letter or number, "
            "and contain only lowercase letters, numbers, dots, and hyphens."
        )
    if _looks_like_ipv4_address(name):
        raise ConfigError("S3_BUCKET_NAME must not be formatted as an IP address.")


def _parse_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    """Application configuration from environment (and optional .env file)."""

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_default_region: str
    s3_bucket_name: str
    aws_session_token: Optional[str] = None
    s3_key_prefix: str = ""
    s3_storage_class: str = "STANDARD"
    s3_acl: Optional[str] = None
    dry_run: bool = False
    log_level: str = "INFO"
    # Repo root (parent of script/) for resolving files/ directory
    project_root: Path = Path.cwd()

    def effective_key_prefix(self, cli_override: Optional[str] = None) -> str:
        """Return key prefix after optional CLI override."""
        if cli_override is not None:
            return _normalize_key_prefix(cli_override)
        return self.s3_key_prefix

    def files_dir(self) -> Path:
        return self.project_root / "files"

    def boto_session_kwargs(self) -> dict:
        """Keyword args for boto3.client/session (no secrets logged elsewhere)."""
        d: dict = {
            "aws_access_key_id": self.aws_access_key_id,
            "aws_secret_access_key": self.aws_secret_access_key,
            "region_name": self.aws_default_region,
        }
        if self.aws_session_token:
            d["aws_session_token"] = self.aws_session_token
        return d


def load_dotenv_file(project_root: Path) -> None:
    """Load `.env` from *project_root* if present."""
    env_path = project_root / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def load_config(
    project_root: Optional[Path] = None,
    require_aws: bool = True,
) -> Config:
    """
    Build :class:`Config` from the environment.

    If *require_aws* is False, AWS-related vars are not required (e.g. list-local only).
    """
    root = project_root or Path.cwd()
    load_dotenv_file(root)

    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"

    key_prefix = _normalize_key_prefix(os.environ.get("S3_KEY_PREFIX"))
    if not key_prefix:
        key_prefix = _normalize_key_prefix(os.environ.get("S3_BUCKET_PREFIX"))

    dry_run = _parse_bool(os.environ.get("DRY_RUN"), default=False)

    storage = (os.environ.get("S3_STORAGE_CLASS") or "STANDARD").strip() or "STANDARD"
    acl_raw = os.environ.get("S3_ACL")
    s3_acl = acl_raw.strip() if acl_raw and acl_raw.strip() else None

    if not require_aws:
        bucket = (os.environ.get("S3_BUCKET_NAME") or "").strip()
        region = (os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        access = os.environ.get("AWS_ACCESS_KEY_ID") or ""
        secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
        token = os.environ.get("AWS_SESSION_TOKEN")
        token = token.strip() if token and token.strip() else None
        return Config(
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            aws_default_region=region,
            s3_bucket_name=bucket,
            aws_session_token=token,
            s3_key_prefix=key_prefix,
            s3_storage_class=storage,
            s3_acl=s3_acl,
            dry_run=dry_run,
            log_level=log_level,
            project_root=root.resolve(),
        )

    required = {
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY"),
        "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION"),
        "S3_BUCKET_NAME": os.environ.get("S3_BUCKET_NAME"),
    }
    missing = [k for k, v in required.items() if not (v and str(v).strip())]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and set values."
        )

    bucket = required["S3_BUCKET_NAME"].strip()
    validate_bucket_name(bucket)

    token = os.environ.get("AWS_SESSION_TOKEN")
    token = token.strip() if token and token.strip() else None

    return Config(
        aws_access_key_id=required["AWS_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=required["AWS_SECRET_ACCESS_KEY"].strip(),
        aws_default_region=required["AWS_DEFAULT_REGION"].strip(),
        s3_bucket_name=bucket,
        aws_session_token=token,
        s3_key_prefix=key_prefix,
        s3_storage_class=storage,
        s3_acl=s3_acl,
        dry_run=dry_run,
        log_level=log_level,
        project_root=root.resolve(),
    )


def merge_dry_run(config: Config, cli_dry_run: Optional[bool]) -> Config:
    """Return a copy of *config* with dry_run overridden by CLI if given."""
    if cli_dry_run is None:
        return config
    from dataclasses import replace

    return replace(config, dry_run=cli_dry_run)
