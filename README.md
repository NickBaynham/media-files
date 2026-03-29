# media-files

A small, production-minded CLI for syncing a local **`files/`** directory to Amazon S3. Upload media (or any files), skip unchanged objects when possible, list what you have locally, and remove objects or buckets with explicit, prefix-aware safety checks.

**Security:** Never commit secrets. Copy `.env.example` to `.env`, keep `.env` git-ignored, and use IAM policies with least privilege. Objects are **not** made public by default; uploads use **SSE-S3** (`AES256`) unless you change the code.

## Make shortcuts

From the repository root, if you have `make` installed:

| Command | Runs |
|---------|------|
| `make upload` | `python script/cli.py upload` |
| `make list` | `python script/cli.py list-local` |
| `make delete` | `python script/cli.py delete-objects` (prompts for confirmation unless you pass flags via the CLI directly) |

The Makefile prefers **`.venv/bin/python`** when that interpreter exists; otherwise it uses **`python3`**. Extra flags still work by calling the CLI yourself, for example `python script/cli.py delete-objects --force`.

## Features

- **list-local** — Recursive scan of `files/` with byte sizes and human-readable totals (GitHub-flavored table via `tabulate`).
- **upload** — Create the bucket if missing (including **us-east-1**), upload recursively, preserve relative paths as keys, optional **key prefix**, **dry-run**, and **skip unchanged** (size + single-part MD5 ETag when safe).
- **delete-objects** — Delete only the S3 keys that correspond to your **current local files** under `files/` (scoped to your configured prefix). Confirmation unless `--force`.
- **delete-bucket** — Remove an **empty** bucket only, with optional **empty-under-prefix-first** flow so you do not wipe unrelated keys outside your prefix.
- **cleanup** — `delete-objects` then optionally delete the bucket (same safety flags as above).
- **bucket-info** — Existence, region, object counts / approximate sizes, and effective prefixes.
- **bucket-exists** — Prints `exists` or `missing`; exit code `0` if the bucket exists, `1` if not (for scripts).
- **CLI** — `argparse` subcommands, `--dry-run`, `--force`, `--prefix`, `--verbose`, and clear exit codes (`0` success, `1` runtime error, `2` configuration error, `130` interrupt).

## Requirements

- **Python 3.10+** (uses type hints and `dataclasses`; 3.9 may work but is not targeted).
- An AWS account, an IAM identity with appropriate S3 permissions, and a globally unique bucket name.

## Project layout

```text
media-files/
  .env.example      # Template for environment variables (commit this)
  .gitignore
  Makefile          # Shortcuts: make upload, make list, make delete
  README.md
  requirements.txt
  script/
    __init__.py
    cli.py           # Main entry point
    config.py        # Env loading and validation
    s3_utils.py      # boto3 helpers
    utils.py         # Paths, sizes, prompts, scanning
    upload_media.py
    delete_media.py
    list_media.py
  files/             # Your local assets (create locally; ignored by git)
```

The CLI resolves the project root as the **parent of `script/`** by default, so `files/` and `.env` are expected next to `script/` (the repository root when you clone this repo).

## Installation

```bash
cd media-files
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real AWS credentials and bucket name
```

After that you can use **`make list`**, **`make upload`**, and **`make delete`** from the repo root (see [Make shortcuts](#make-shortcuts)).

Install dependencies only:

```bash
pip install -r requirements.txt
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_ACCESS_KEY_ID` | Yes* | Access key for an IAM user or temporary credentials. |
| `AWS_SECRET_ACCESS_KEY` | Yes* | Secret key. |
| `AWS_DEFAULT_REGION` | Yes* | Region for the S3 client and bucket creation. |
| `S3_BUCKET_NAME` | Yes* | Globally unique bucket name (lowercase, 3–63 chars; validated). |
| `AWS_SESSION_TOKEN` | No | Session token when using STS temporary credentials. |
| `S3_KEY_PREFIX` | No | Logical “folder” prefix for object keys (no leading `/`). |
| `S3_BUCKET_PREFIX` | No | Legacy alias: used as key prefix if `S3_KEY_PREFIX` is unset. |
| `S3_STORAGE_CLASS` | No | Default `STANDARD` (e.g. `STANDARD_IA`, `INTELLIGENT_TIERING`). |
| `S3_ACL` | No | If unset, no ACL is sent (private / bucket defaults). Avoid `public-read` unless intentional. |
| `DRY_RUN` | No | If `true`, upload/delete paths log only (CLI `--dry-run` overrides for that run). |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`). |

\*Not required for **`list-local`**, which only reads the filesystem.

Place variables in `.env` at the repo root; the app loads them with `python-dotenv`.

## IAM example (least privilege sketch)

Adjust ARNs and prefixes to your account. This policy allows managing **one bucket** and objects under an optional key prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucketScoped",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::your-unique-media-bucket-name",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["", "media/v1/*"]
        }
      }
    },
    {
      "Sid": "ObjectRWScoped",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::your-unique-media-bucket-name/media/v1/*"
    },
    {
      "Sid": "BucketLifecycleOptional",
      "Effect": "Allow",
      "Action": ["s3:CreateBucket", "s3:HeadBucket", "s3:GetBucketLocation", "s3:DeleteBucket"],
      "Resource": "arn:aws:s3:::your-unique-media-bucket-name"
    }
  ]
}
```

If you use **no** key prefix, scope `s3:prefix` and object ARNs to `*` only if you accept broader access. For **create/delete bucket**, `s3:CreateBucket` and `s3:DeleteBucket` must be allowed on that bucket ARN.

## Usage

Run from the repository root (or pass `--project-root`). Either form works:

```bash
python script/cli.py <subcommand> ...
python -m script.cli <subcommand> ...
```

Common flows via **Make** (see [Make shortcuts](#make-shortcuts)):

```bash
make list
make upload
make delete
```

### List local files

```bash
make list
python script/cli.py list-local
python script/cli.py list-local -v
```

Example output:

```text
| Path (under files/)   |   Bytes | Size    |
|-----------------------|---------|---------|
| images/logo.png       |   12044 | 11.76 KB|
| — TOTAL —             |   12044 | 11.76 KB|

Files: 1
```

If `files/` is missing, the tool prints a short message and exits successfully with nothing to list.

### Upload

```bash
make upload
python script/cli.py upload
python script/cli.py upload --dry-run
python script/cli.py upload --prefix media/v1
```

Flow: validate config → ensure bucket exists (create if needed) → for each file, **skip** if size and MD5 ETag match (multipart ETags force re-upload for safety) → upload with SSE-S3 and optional storage class / ACL.

### Delete objects (local-mapped keys only)

```bash
make delete
python script/cli.py delete-objects
python script/cli.py delete-objects --dry-run
python script/cli.py delete-objects --force
```

Deletes **only** keys built from your current `files/` tree and the effective key prefix — not every object in the bucket.

### Delete bucket

```bash
python script/cli.py delete-bucket
python script/cli.py delete-bucket --force
python script/cli.py delete-bucket --empty-under-prefix-first
python script/cli.py delete-bucket --empty-under-prefix-first --full-bucket
```

- Default: bucket must be **completely empty**.
- `--empty-under-prefix-first`: delete all objects whose keys start with your configured prefix, then delete the bucket **only if no objects remain** (protects keys outside your prefix).
- If `S3_KEY_PREFIX` is empty, `--full-bucket` is **required** with `--empty-under-prefix-first` to delete all objects first (explicit opt-in).

### Cleanup

```bash
python script/cli.py cleanup
python script/cli.py cleanup --delete-bucket
python script/cli.py cleanup --delete-bucket --empty-under-prefix-first --force
```

Runs **delete-objects** first, then optional bucket deletion with the same rules as **delete-bucket**.

### Bucket info and existence

```bash
python script/cli.py bucket-info
python script/cli.py bucket-exists
echo $?   # 0 = exists, 1 = missing or error
```

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `Missing required environment variables` | `.env` path, variable names, and that you are using the repo root (or `--project-root`). |
| `Access denied when checking the bucket` | IAM must allow `s3:HeadBucket` (and list/get as needed). |
| `Bucket name is already taken globally` | Pick another `S3_BUCKET_NAME`. |
| Wrong region / wrong endpoint | `AWS_DEFAULT_REGION` must match where the bucket lives. |
| Uploads always run | Multipart uploads produce composite ETags; the tool re-uploads when ETag is not a simple MD5. |
| `files/` ignored in git | Intended: add assets locally; do not commit large binaries unless you use another workflow. |

## Security notes

- Do **not** commit `.env` or AWS keys. The README and logs avoid printing full secrets.
- Default behavior avoids **public-read** ACLs; set `S3_ACL` only when you understand the exposure.
- Destructive commands require confirmation unless `--force`; dry-run skips confirmation for convenience.
- Bucket deletion refuses to run if the bucket still contains objects (unless you use the documented emptying flags).

## Future enhancements (not implemented)

- Multipart upload for very large files and stronger change detection (e.g. checksum algorithm headers).
- `sync` subcommand with delete extraneous remote keys (dangerous; needs careful UX).
- Progress bars and parallel uploads.
- Optional `pyproject.toml` / `pipx` console script entry point.

## License

Use and modify freely for your own projects; add a `LICENSE` file if you publish the repo (MIT is a common choice).
