"""S3 wrapper for activity context files.

Issues presigned PUT URLs (frontend uploads directly to S3), HEAD-checks
objects after upload, and deletes objects on file removal. Embedding
ingestion is handled separately by the milo-ingest worker, which reacts to
S3 event notifications via SQS — see milo-ingest/src/main.py.

KEY PREFIXING
-------------
When ``S3_KEY_PREFIX`` is set (e.g. ``milo/``) every object operation in
this module transparently prepends it to the caller-supplied key. This lets
us share a bucket with other apps without colliding on key names:

    DB row.s3_key  =  "activities/<id>/<file_id>/file.pdf"   (logical)
    S3 object      =  "milo/activities/<id>/<file_id>/file.pdf"

The prefix is purely an S3-storage concern — the DB still stores the
unprefixed logical key, and callers continue to pass logical keys here.
Unset prefix = no change, preserves backwards compatibility with the
LocalStack/dev setup and existing deployments. The trailing slash is
normalised; ``milo``, ``milo/``, and ``/milo/`` all behave identically.

If you enable a prefix and you also use the milo-ingest worker, make sure
the ingest worker strips the same prefix before looking up the DB row by
key (or use an S3 event-notification filter scoped to the prefix so the
worker only sees events it owns).
"""

import logging
import os
from functools import lru_cache
from typing import Mapping

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

logger = logging.getLogger("milo-orchestrator.s3")


def _bucket() -> str:
    bucket = os.getenv("S3_ACTIVITY_FILES_BUCKET")
    if not bucket:
        raise RuntimeError("S3_ACTIVITY_FILES_BUCKET is not configured")
    return bucket


def _key_prefix() -> str:
    """Normalised S3 key prefix. Empty string when unset (no prefixing)."""
    raw = os.getenv("S3_KEY_PREFIX", "")
    if not raw:
        return ""
    # Strip leading slashes (S3 keys never start with /) and ensure exactly
    # one trailing slash so concatenation gives a clean "prefix/key".
    return raw.lstrip("/").rstrip("/") + "/"


def _full_key(key: str) -> str:
    """Apply the optional ``S3_KEY_PREFIX`` to a logical key.

    Idempotent: if the caller has already passed a prefixed key (e.g. from an
    S3 event that already carries the full key), we don't double-prefix it.
    """
    prefix = _key_prefix()
    if not prefix:
        return key
    if key.startswith(prefix):
        return key
    return f"{prefix}{key.lstrip('/')}"


@lru_cache(maxsize=1)
def get_s3_client():
    """Build a boto3 S3 client. Sigv4 is required for presigned PUTs that
    enforce object metadata. Honors AWS_ENDPOINT_URL for LocalStack/dev."""
    endpoint = os.getenv("AWS_ENDPOINT_URL") or None
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=endpoint,
        config=Config(signature_version="s3v4"),
    )


def generate_presigned_put(
    key: str,
    content_type: str,
    content_length: int,
    metadata: Mapping[str, str],
    expires_in: int = 900,
) -> str:
    """Return a presigned URL for PUT-uploading an object with required
    metadata headers signed in. The client MUST send the same Content-Type
    and x-amz-meta-* headers it received in `required_headers`, otherwise
    S3 rejects the upload (signature mismatch)."""
    client = get_s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _bucket(),
            "Key": _full_key(key),
            "ContentType": content_type,
            "ContentLength": content_length,
            "Metadata": dict(metadata),
        },
        ExpiresIn=expires_in,
    )


def head_object(key: str) -> dict | None:
    """Return the head_object response, or None if the object does not exist."""
    client = get_s3_client()
    try:
        return client.head_object(Bucket=_bucket(), Key=_full_key(key))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def delete_object(key: str) -> None:
    """Delete the object. S3 ObjectRemoved event fan-outs to milo-ingest's
    SQS queue, which removes the corresponding embeddings."""
    client = get_s3_client()
    try:
        client.delete_object(Bucket=_bucket(), Key=_full_key(key))
    except ClientError:
        logger.exception("Failed to delete S3 object %s", _full_key(key))
        raise
