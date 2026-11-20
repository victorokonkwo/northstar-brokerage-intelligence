"""S3-compatible object store adapter (SeaweedFS locally, S3 in the cloud).

Writes are conditional (``If-None-Match: *``), so an existing object is never
overwritten: the store itself refuses the write with ``412 Precondition
Failed``. That behaviour is verified against SeaweedFS 4.54 (docs/sources.md)
and covered by an integration test.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

# boto3 and botocore ship without type information. Only the S3 client is typed
# (mypy-boto3-s3), so the untyped imports stay contained in this adapter.
import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from northstar.common.exceptions import ConfigurationError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

DEFAULT_BUCKET = "northstar-data-local"
"""The local bucket SeaweedFS creates on start (infrastructure/docker/compose.yaml)."""


@dataclass(frozen=True, slots=True)
class ObjectStoreSettings:
    """Connection settings for the object store. Secrets are never logged."""

    endpoint_url: str | None
    access_key_id: str | None
    secret_access_key: str | None
    bucket: str
    region: str = "us-east-1"  # SeaweedFS ignores the region; boto3 requires one

    @classmethod
    def aws(cls, bucket: str, region: str) -> "ObjectStoreSettings":
        """Amazon S3 itself, with no endpoint override.

        Credentials come from the standard AWS chain (the environment, or a
        task role on ECS), never from this object.
        """
        return cls(None, None, None, bucket, region)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ObjectStoreSettings":
        """Read the ``NBI_S3_*`` variables (see ``.env.example``).

        Raises:
            ConfigurationError: A variable is missing.
        """
        env = os.environ if environ is None else environ
        names = ("NBI_S3_ENDPOINT_URL", "NBI_S3_ACCESS_KEY_ID", "NBI_S3_SECRET_ACCESS_KEY")
        missing = [name for name in names if not env.get(name)]
        if missing:
            msg = f"missing environment variables: {', '.join(missing)} (run `make env`)"
            raise ConfigurationError(msg)
        return cls(
            endpoint_url=env["NBI_S3_ENDPOINT_URL"],
            access_key_id=env["NBI_S3_ACCESS_KEY_ID"],
            secret_access_key=env["NBI_S3_SECRET_ACCESS_KEY"],
            bucket=env.get("NBI_S3_BUCKET") or DEFAULT_BUCKET,
        )


class S3ObjectStore:
    """Put-if-absent and get over one bucket, optionally under a key prefix."""

    def __init__(self, settings: ObjectStoreSettings, prefix: str = "") -> None:
        """Create a client. ``prefix`` isolates keys (tests use a unique one)."""
        self._bucket = settings.bucket
        self._prefix = prefix
        if settings.endpoint_url is None:  # Amazon S3: default addressing and credential chain
            client = boto3.client(
                "s3", region_name=settings.region, config=Config(retries={"mode": "standard"})
            )
        else:
            client = boto3.client(
                "s3",
                endpoint_url=settings.endpoint_url,
                aws_access_key_id=settings.access_key_id,
                aws_secret_access_key=settings.secret_access_key,
                region_name=settings.region,
                config=Config(s3={"addressing_style": "path"}, retries={"mode": "standard"}),
            )
        self._client = cast("S3Client", client)

    def put_if_absent(self, key: str, body: bytes) -> bool:
        """Write ``body`` at ``key`` unless an object exists there; return whether it was written.

        Raises:
            botocore.exceptions.ClientError: Any failure other than "already exists".
        """
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=self._prefix + key, Body=body, IfNoneMatch="*"
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "PreconditionFailed":
                return False
            raise
        return True

    def get(self, key: str) -> bytes:
        """Read the object at ``key``.

        Raises:
            botocore.exceptions.ClientError: The object is missing or unreadable.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=self._prefix + key)
        body: bytes = response["Body"].read()
        return body
