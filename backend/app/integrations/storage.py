"""Document storage on S3 (LLD section 10)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import Clock

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=10)
DOWNLOAD_TTL = timedelta(minutes=5)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitise_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    base = _SAFE.sub("_", base).strip("._") or "file"
    return base[:100]


def object_key(org_id: str, owner_type: str, owner_id: str, document_id: str, filename: str) -> str:
    return f"org/{org_id}/{owner_type}/{owner_id}/{document_id}/{sanitise_filename(filename)}"


def parse_key(key: str) -> tuple[str, str] | None:
    """(org_id, document_id) from an object key, or None if it is not one of ours."""
    parts = key.split("/")
    if len(parts) != 6 or parts[0] != "org":
        return None
    return parts[1], parts[4]


class DocumentStorage:
    def __init__(
        self, s3: Any, signer: Any, bucket: str, clock: Clock, kms_key_id: str | None
    ) -> None:
        # `signer` only signs URLs (no network): it may use the browser-facing endpoint.
        self._s3, self._signer, self.bucket, self._clock, self._kms = (
            s3,
            signer,
            bucket,
            clock,
            kms_key_id,
        )

    def presign_upload(
        self, key: str, content_type: str, size_bytes: int
    ) -> tuple[dict[str, Any], datetime]:
        expires_at = self._clock.now() + UPLOAD_TTL
        fields = {"Content-Type": content_type}
        conditions: list[Any] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, min(size_bytes, MAX_UPLOAD_BYTES)],
        ]
        if self._kms:
            fields["x-amz-server-side-encryption"] = "aws:kms"
            conditions.append({"x-amz-server-side-encryption": "aws:kms"})
        post = self._signer.generate_presigned_post(
            self.bucket,
            key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=int(UPLOAD_TTL.total_seconds()),
        )
        return post, expires_at

    def presign_download(self, key: str, filename: str, content_type: str) -> tuple[str, datetime]:
        url = self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{sanitise_filename(filename)}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=int(DOWNLOAD_TTL.total_seconds()),
        )
        return url, self._clock.now() + DOWNLOAD_TTL

    def put(self, key: str, body: bytes, content_type: str) -> None:
        extra: dict[str, str] = {}
        if self._kms:
            extra = {"ServerSideEncryption": "aws:kms"}
        self._s3.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, **extra
        )

    def head(self, key: str) -> dict[str, Any] | None:
        try:
            r: dict[str, Any] = self._s3.head_object(Bucket=self.bucket, Key=key)
            return r
        except self._s3.exceptions.ClientError:
            return None

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)

    def read(self, key: str) -> bytes:
        """Small files served by the API itself (organisation logos)."""
        body: bytes = self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return body

    def rename(self, key: str, new_key: str) -> bool:
        """Copy then delete. Safe to repeat: returns False when there is nothing to move."""
        try:
            self._s3.copy_object(
                Bucket=self.bucket, Key=new_key, CopySource={"Bucket": self.bucket, "Key": key}
            )
        except self._s3.exceptions.ClientError:
            return False  # already moved, or the file was never uploaded
        self._s3.delete_object(Bucket=self.bucket, Key=key)
        return True

    def tag(self, key: str, verdict: str) -> None:
        self._s3.put_object_tagging(
            Bucket=self.bucket, Key=key, Tagging={"TagSet": [{"Key": "scan", "Value": verdict}]}
        )

    def quarantine(self, key: str) -> str:
        """Move an infected object under quarantine/ so no presigned path can reach it."""
        target = f"quarantine/{key}"
        self._s3.copy_object(
            Bucket=self.bucket, Key=target, CopySource={"Bucket": self.bucket, "Key": key}
        )
        self._s3.delete_object(Bucket=self.bucket, Key=key)
        return target
