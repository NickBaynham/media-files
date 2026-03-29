# media-files

A small, production-minded CLI for syncing a local **`files/`** directory to Amazon S3. Upload media (or any files), skip unchanged objects when possible, **list local files** and **list what is in the bucket**, and remove objects or buckets with explicit, prefix-aware safety checks.

**Security:** Never commit secrets. Copy `.env.example` to `.env`, keep `.env` git-ignored, and use IAM policies with least privilege. Objects are **not** made public by default; uploads use **SSE-S3** (`AES256`) unless you change the code.

## Make shortcuts

From the repository root, if you have `make` installed:

| Command | Runs |
|---------|------|
| `make upload` | `python script/cli.py upload` (private / default bucket policy) |
| `make upload-public` | `python script/cli.py upload --public` (canned ACL **public-read** for static sites / Amplify—see [Public uploads](#public-uploads-for-websites-amplify)) |
| `make list-local` | `python script/cli.py list-local` — files on disk under **`files/`** only (no AWS). |
| `make list-uploaded` | `python script/cli.py list-uploaded` — objects in **`S3_BUCKET_NAME`** (under **`S3_KEY_PREFIX`** by default). |
| `make check-public` | `python script/cli.py check-public` — anonymous **HTTP HEAD** per object (see [Check public access](#check-public-access)). |
| `make delete` | `python script/cli.py delete-objects` (prompts for confirmation unless you pass flags via the CLI directly) |
| `make remove-bucket` | `python script/cli.py remove-bucket` — deletes **all** objects in **`S3_BUCKET_NAME`** (every prefix), then deletes the bucket (full reset; see [Remove bucket](#remove-bucket-full-reset)) |

The Makefile prefers **`.venv/bin/python`** when that interpreter exists; otherwise it uses **`python3`**. Extra flags still work by calling the CLI yourself, for example `python script/cli.py delete-objects --force`.

## Features

- **list-local** — Recursive scan of `files/` with byte sizes and human-readable totals (GitHub-flavored table via `tabulate`). No AWS credentials.
- **list-uploaded** — Table of object keys and sizes in the configured bucket; by default only under **`S3_KEY_PREFIX`**. Use **`--all-bucket`** to list the whole bucket (see [List uploaded objects](#list-uploaded-objects-in-s3)).
- **check-public** — Unauthenticated **HTTP HEAD** against each object’s URL (**`200`** ≈ publicly readable, **`403`** ≈ private or blocked). **`make check-public`**.
- **upload** — Create the bucket if missing (including **us-east-1**), upload recursively, preserve relative paths as keys, optional **key prefix**, **dry-run**, and **skip unchanged** (size + single-part MD5 ETag when safe). **`--public`** (or **`make upload-public`**) sets canned ACL **public-read** for website/CDN use and re-uploads every file so the ACL applies.
- **delete-objects** — Delete only the S3 keys that correspond to your **current local files** under `files/` (scoped to your configured prefix). Confirmation unless `--force`.
- **remove-bucket** — Delete **every** object in the configured bucket (ignores **`S3_KEY_PREFIX`**), then delete the bucket. For a full reset (for example before recreating the bucket with different public settings). **`make remove-bucket`**.
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
  Makefile          # Shortcuts: upload, upload-public, list-local, list-uploaded, check-public, delete, remove-bucket
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

After that you can use **`make list-local`**, **`make list-uploaded`**, **`make check-public`**, **`make upload`**, **`make upload-public`** (optional), **`make delete`**, and **`make remove-bucket`** (destructive full reset) from the repo root (see [Make shortcuts](#make-shortcuts)).

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
| `S3_ACL` | No | If unset, no ACL is sent (private / bucket defaults). Prefer **`python script/cli.py upload --public`** or **`make upload-public`** for explicit public assets instead of leaving `public-read` permanently in `.env`. |
| `DRY_RUN` | No | If `true`, upload/delete paths log only (CLI `--dry-run` overrides for that run). |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`). |

\*Not required for **`list-local`** (disk only). **`list-uploaded`** and all other S3 commands need the starred variables.

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
        "s3:PutObjectAcl",
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
    },
    {
      "Sid": "PublicUploadAutoPolicy",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketPolicy",
        "s3:PutBucketPolicy",
        "s3:PutPublicAccessBlock"
      ],
      "Resource": "arn:aws:s3:::your-unique-media-bucket-name"
    }
  ]
}
```

If you use **no** key prefix, scope `s3:prefix` and object ARNs to `*` only if you accept broader access. For **create/delete bucket**, `s3:CreateBucket` and `s3:DeleteBucket` must be allowed on that bucket ARN. Include **`s3:PutObjectAcl`** only if you use **`upload --public`** / **`make upload-public`** and the bucket allows ACLs; otherwise you can omit it. For **automatic public bucket policies** on **`upload --public`**, allow **`s3:GetBucketPolicy`**, **`s3:PutBucketPolicy`**, and **`s3:PutPublicAccessBlock`** on the bucket ARN (see **PublicUploadAutoPolicy** in the example). Omit that statement if you use **`--no-bucket-policy`** and manage policy only in the console.

## Usage

Run from the repository root (or pass `--project-root`). Either form works:

```bash
python script/cli.py <subcommand> ...
python -m script.cli <subcommand> ...
```

Common flows via **Make** (see [Make shortcuts](#make-shortcuts)):

```bash
make list-local
make list-uploaded
make check-public
make upload
make upload-public   # optional: public-read ACL for static hosting / Amplify
make delete
make remove-bucket   # destructive: wipes entire bucket from .env then deletes it
```

### List local files (`files/` on disk)

```bash
make list-local
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

### List uploaded objects in S3

```bash
make list-uploaded
python script/cli.py list-uploaded
python script/cli.py list-uploaded --prefix media/v1
python script/cli.py list-uploaded --all-bucket
```

By default, keys are listed only **under your configured `S3_KEY_PREFIX`** (same scope as uploads). With **`--all-bucket`**, every object in the bucket is listed (useful when you have a prefix set but want to inspect the whole bucket). Requires **`s3:ListBucket`** (and listing under a prefix may require a matching `s3:prefix` condition in IAM).

### Check public access

After **`upload --public`**, object ACLs may be skipped (see [Public uploads](#public-uploads-for-websites-amplify)). This command **does not use your AWS keys** for the read test: it only sends an **anonymous HTTP HEAD** to each object’s virtual-hosted URL (using **`AWS_DEFAULT_REGION`** in the hostname).

```bash
make check-public
python script/cli.py check-public
python script/cli.py check-public --all-bucket
python script/cli.py check-public --limit 50
```

- **HTTP 200** — Anonymous read is allowed (bucket policy / public ACL path is working for that key).
- **HTTP 403** — Not publicly readable (typical for private objects, Block Public Access, or missing **`s3:GetObject`** in a bucket policy).

Use **`--limit 0`** to probe every object (can be slow on large buckets).

### Upload

```bash
make upload
python script/cli.py upload
python script/cli.py upload --dry-run
python script/cli.py upload --prefix media/v1
```

Flow: validate config → ensure bucket exists (create if needed) → for each file, **skip** if size and MD5 ETag match (multipart ETags force re-upload for safety) → upload with SSE-S3 and optional storage class / ACL from `S3_ACL` when set.

### Public uploads (websites / Amplify)

Use this when objects must be **readable anonymously** via HTTPS (for example static assets referenced from **AWS Amplify** or another site).

```bash
make upload-public
python script/cli.py upload --public
python script/cli.py upload --public --dry-run
```

**Behavior**

- Every file under `files/` is uploaded (skip-if-unchanged is **off**).
- If the bucket was **just created** in this run, or **has no bucket policy** yet, the tool attaches an anonymous **`s3:GetObject`** policy on the effective key prefix (or `/*` if the prefix is empty) and relaxes **Block Public Access** only enough to allow **policy-based** public reads (public **ACLs** stay blocked). Use **`--no-bucket-policy`** on **`upload`** to skip this. If a policy **already exists**, it is **not** replaced—merge **`GetObject`** in the console if you still see **403**.
- The CLI then tries the canned ACL **`public-read`** on each object. If the bucket returns **`AccessControlListNotSupported`**, it **re-uploads without an ACL**; anonymous access then relies on the bucket policy above.
- After a real (non–dry-run) public upload, the tool runs an **anonymous HTTP HEAD** against each uploaded object’s URL. If every probe returns **HTTP 200**, it logs and prints that **public access is validated**; otherwise it **warns** which keys are not anonymously readable (typical **403** until a bucket policy is added). **`--dry-run`** skips this check.
- You still get SSE-S3 encryption at rest (`AES256`). “Public” means **unauthenticated read** is allowed—either via object ACL or via bucket policy.

**Bucket policy example (no object ACLs)**

After `upload --public` falls back (or if you use plain `make upload`), attach a policy like this in **S3 → Bucket → Permissions → Bucket policy**. Replace the bucket name and adjust the `Resource` ARN if you use a key prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"
    }
  ]
}
```

You may need to turn off **Block Public Access** settings that block bucket policies (for example “Block public access to buckets and objects granted through new public bucket policies”). Scope `Resource` to a prefix (e.g. `arn:aws:s3:::my-bucket/public/*`) when possible.

**AWS console settings (ACL path only)**

If you want **`public-read`** object ACLs to work instead of a policy, use **Object Ownership: Bucket owner preferred** (ACLs enabled) and adjust **Block Public Access** so public ACLs are allowed. That is optional now that **`upload --public`** falls back without ACLs.

**IAM**

- **`s3:PutObject`** is always required for uploads.
- Add **`s3:PutObjectAcl`** only if the bucket accepts ACLs and you rely on the first-step `public-read` attempt (see the example policy above).

### Delete objects (local-mapped keys only)

```bash
make delete
python script/cli.py delete-objects
python script/cli.py delete-objects --dry-run
python script/cli.py delete-objects --force
```

Deletes **only** keys built from your current `files/` tree and the effective key prefix — not every object in the bucket.

### Remove bucket (full reset)

Use this when you want to **drop the entire bucket** named in **`S3_BUCKET_NAME`**: every object (all prefixes) is deleted, then the bucket itself is removed. **`S3_KEY_PREFIX` is ignored** so nothing is left behind. Afterward, **`make upload`** can create a fresh bucket again.

```bash
make remove-bucket
python script/cli.py remove-bucket
python script/cli.py remove-bucket --dry-run
python script/cli.py remove-bucket --force
```

You are prompted once before any deletion (unless **`--force`**). **`--dry-run`** prints the plan and simulates without changing S3.

**IAM:** needs **`s3:ListBucket`**, **`s3:DeleteObject`** on `arn:aws:s3:::BUCKET/*`, and **`s3:DeleteBucket`** on `arn:aws:s3:::BUCKET`.

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
| `AccessControlListNotSupported` on `--public` | **`upload --public`** retries without ACL and should still finish; add a **bucket policy** for `s3:GetObject` (README). If you see this on a normal `upload` with `S3_ACL` set, remove the ACL from `.env` or switch to `--public`. |
| Public URL still 403 | Object ACL may be correct but bucket policy or Block Public Access may still block anonymous access. |
| `files/` ignored in git | Intended: add assets locally; do not commit large binaries unless you use another workflow. |

## Security notes

- Do **not** commit `.env` or AWS keys. The README and logs avoid printing full secrets.
- Default **`make upload`** does **not** use `public-read`. Use **`make upload-public`** or **`upload --public`** only when you intend anonymous read access to those objects.
- **`S3_ACL=public-read`** in `.env` affects every normal upload; prefer the explicit **`--public`** flag when you only sometimes need public objects.
- Destructive commands require confirmation unless `--force`; dry-run skips confirmation for convenience.
- **`delete-bucket`** refuses to run if the bucket still contains objects (unless you use the documented emptying flags). **`remove-bucket` / `make remove-bucket`** is the intentional “wipe everything” path—verify **`S3_BUCKET_NAME`** in `.env` before running it.

## Future enhancements (not implemented)

- Multipart upload for very large files and stronger change detection (e.g. checksum algorithm headers).
- `sync` subcommand with delete extraneous remote keys (dangerous; needs careful UX).
- Progress bars and parallel uploads.
- Optional `pyproject.toml` / `pipx` console script entry point.

## License

Use and modify freely for your own projects; add a `LICENSE` file if you publish the repo (MIT is a common choice).
