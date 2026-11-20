"""Amazon Redshift Serverless: connections, the S3 load stage, and idempotent loads.

**Connections** use temporary IAM credentials (``redshift-serverless
get-credentials``), so the pipeline has no stored database password. The
database user is the caller's IAM identity (``IAM:<user>`` or
``IAMR:<role>``). Only ``redshift-bootstrap`` connects as the admin, whose
password Redshift keeps in Secrets Manager.

**Loads** have the same contract as ``postgres.insert_new``: rows whose key is
new are inserted, a replay inserts nothing, and a stored row with the same
key but different content is refused with ``LoadConflictError``. Redshift has
no ``COPY FROM STDIN`` and no ``ON CONFLICT``, so the rows are written as JSON
Lines to the bronze bucket's ``_stage/`` prefix (content-addressed, so a
replay reuses the object), copied into a temp table, checked, and inserted
where the key is not stored yet.

**Append-only** is enforced by ownership and grants (owner decision
2026-11-20): the admin owns every append-only table, and the pipeline's user
holds only USAGE, SELECT, and INSERT on them (``bootstrap``).
"""

import hashlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

# boto3 ships without type information (see object_store.py).
import boto3  # type: ignore[import-untyped]
import psycopg
from psycopg import sql

from northstar.common.exceptions import ConfigurationError
from northstar.infrastructure.dialect import RedshiftConnection
from northstar.infrastructure.loading import LoadConflictError, distinct_rows
from northstar.infrastructure.object_store import ObjectStoreSettings, S3ObjectStore

STAGE_PREFIX: Final = "_stage/"
"""Where load files are staged in the bronze bucket (not part of bronze itself)."""

PIPELINE_ROLE: Final = "nbi_pipeline"
"""The database role the pipeline's users get: USAGE, SELECT, INSERT, and nothing else."""


@dataclass(frozen=True, slots=True)
class Stage:
    """Where ``COPY`` reads load files from, and the IAM role it reads them with."""

    store: S3ObjectStore
    bucket: str
    iam_role: str = "default"  # the namespace's default role (Terraform data stack)


@dataclass(frozen=True, slots=True)
class RedshiftSettings:
    """Connection settings for Redshift Serverless. Credentials are never stored."""

    host: str
    port: int
    dbname: str
    workgroup: str
    region: str
    bucket: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RedshiftSettings":
        """Read the ``NBI_RS_*`` variables and ``NBI_BRONZE_BUCKET`` (see ``.env.example``).

        Raises:
            ConfigurationError: A variable is missing or the port is not a number.
        """
        env = os.environ if environ is None else environ
        missing = [n for n in ("NBI_RS_HOST", "NBI_BRONZE_BUCKET") if not env.get(n)]
        if missing:
            msg = f"missing environment variables: {', '.join(missing)} (see .env.example)"
            raise ConfigurationError(msg)
        try:
            port = int(env.get("NBI_RS_PORT", "5439"))
        except ValueError as error:
            msg = f"NBI_RS_PORT must be a number, got {env['NBI_RS_PORT']!r}"
            raise ConfigurationError(msg) from error
        return cls(
            host=env["NBI_RS_HOST"],
            port=port,
            dbname=env.get("NBI_RS_DATABASE", "northstar"),
            workgroup=env.get("NBI_RS_WORKGROUP", "northstar"),
            region=env.get("NBI_AWS_REGION", "ca-central-1"),
            bucket=env["NBI_BRONZE_BUCKET"],
        )

    def connect(self) -> RedshiftConnection:
        """Connect as the caller's IAM identity, with temporary credentials (TLS only)."""
        client = boto3.client("redshift-serverless", region_name=self.region)
        creds = client.get_credentials(
            workgroupName=self.workgroup, dbName=self.dbname, durationSeconds=3600
        )
        return self._open(str(creds["dbUser"]), str(creds["dbPassword"]))

    def connect_admin(self, secret_arn: str) -> RedshiftConnection:
        """Connect as the namespace admin, whose credentials Redshift keeps in Secrets Manager."""
        client = boto3.client("secretsmanager", region_name=self.region)
        secret = json.loads(client.get_secret_value(SecretId=secret_arn)["SecretString"])
        return self._open(str(secret["username"]), str(secret["password"]), admin=True)

    def _open(self, user: str, password: str, *, admin: bool = False) -> RedshiftConnection:
        conn = RedshiftConnection.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=user,
            password=password,
            sslmode="require",
            connect_timeout=60,
        )
        store = S3ObjectStore(ObjectStoreSettings.aws(self.bucket, self.region), STAGE_PREFIX)
        conn.stage = Stage(store, self.bucket)
        conn.admin = admin
        return conn


def insert_new_redshift(
    conn: RedshiftConnection,
    schema: str,
    table: str,
    key: str,
    columns: Sequence[str],
    content: Sequence[str],
    rows: Sequence[tuple[Any, ...]],
) -> int:
    """``postgres.insert_new`` for Redshift: stage, ``COPY``, check, insert what is new.

    Raises:
        LoadConflictError: The input repeats a key with different content, or a
            stored row differs from the input; nothing is inserted.
    """
    unique = distinct_rows(schema, table, key, rows)
    if not unique:
        return 0
    stage: Stage = conn.stage
    supers = _super_columns(conn, schema, table)
    body = "".join(
        json.dumps(_record(columns, row, supers), sort_keys=True, separators=(",", ":")) + "\n"
        for row in unique
    ).encode("utf-8")
    name = f"{schema}.{table}/{hashlib.sha256(body).hexdigest()}.jsonl"
    stage.store.put_if_absent(name, body)  # content-addressed: an existing object is identical
    target = sql.Identifier(schema, table)
    staging = sql.Identifier(f"_load_{table}")
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    differs = sql.SQL(" or ").join(_differs(c, c in supers) for c in content)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            sql.SQL("create temp table {} (like {} including defaults)").format(staging, target)
        )
        cur.execute(
            sql.SQL(
                "copy {staging} ({cols}) from {path} iam_role {role} "
                "format json 'auto' timeformat 'auto'"
            ).format(
                staging=staging,
                cols=cols,
                path=sql.Literal(f"s3://{stage.bucket}/{STAGE_PREFIX}{name}"),
                role=_iam_role(stage.iam_role),
            )
        )
        cur.execute(
            sql.SQL(
                "select s.{key} from {staging} s join {target} t on s.{key} = t.{key} "
                "where {differs} limit 5"
            ).format(key=sql.Identifier(key), staging=staging, target=target, differs=differs)
        )
        conflicts = [str(r[0]) for r in cur.fetchall()]
        if conflicts:
            msg = f"{schema}.{table}: stored rows differ from the replay for {key} {conflicts}"
            raise LoadConflictError(msg)
        cur.execute(
            sql.SQL(
                "insert into {target} ({cols}) select {cols} from {staging} s "
                "where not exists (select 1 from {target} t where t.{key} = s.{key})"
            ).format(target=target, cols=cols, staging=staging, key=sql.Identifier(key))
        )
        inserted = cur.rowcount
        cur.execute(sql.SQL("drop table {}").format(staging))
    return inserted


def require_tables(conn: psycopg.Connection[Any], schema: str, tables: Sequence[str]) -> None:
    """Check that ``bootstrap`` created ``tables``: the pipeline's user can't create them.

    Raises:
        ConfigurationError: A table is missing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = %s",
            (schema,),
        )
        present = {str(r[0]) for r in cur.fetchall()}
    missing = sorted(set(tables) - present)
    if missing:
        msg = (
            f"{schema}: missing {', '.join(missing)}; the admin creates tables on Redshift "
            "(run `northstar redshift-bootstrap`)"
        )
        raise ConfigurationError(msg)


def grant_pipeline(
    conn: RedshiftConnection,
    schemas: Sequence[str],
    users: Sequence[str],
    lock_tables: Sequence[tuple[str, str]] = (),
) -> None:
    """Give ``users`` the pipeline role: USAGE, SELECT, and INSERT on ``schemas``, nothing more.

    The admin owns the tables, so the pipeline can't UPDATE, DELETE, or
    TRUNCATE them. It may CREATE schemas in the database, for dbt's own
    rebuildable models. ``lock_tables`` are empty tables it may ``LOCK``,
    which needs UPDATE.
    """
    role = sql.Identifier(PIPELINE_ROLE)
    with conn.cursor() as cur:
        cur.execute("select 1 from svv_roles where role_name = %s", (PIPELINE_ROLE,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("create role {}").format(role))
        for schema in schemas:
            name = sql.Identifier(schema)
            cur.execute(sql.SQL("grant usage on schema {} to role {}").format(name, role))
            cur.execute(
                sql.SQL("grant select, insert on all tables in schema {} to role {}").format(
                    name, role
                )
            )
        for schema, table in lock_tables:
            cur.execute(
                sql.SQL("grant update on {} to role {}").format(sql.Identifier(schema, table), role)
            )
        cur.execute(
            sql.SQL("grant create on database {} to role {}").format(
                sql.Identifier(conn.info.dbname), role
            )
        )
        for user in users:
            cur.execute(sql.SQL("grant role {} to {}").format(role, sql.Identifier(user)))


def _iam_role(role: str) -> sql.Composable:
    """``default`` (the namespace's default role) as a keyword, any other ARN as a literal."""
    return sql.SQL("default") if role == "default" else sql.Literal(role)


def _super_columns(conn: psycopg.Connection[Any], schema: str, table: str) -> frozenset[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = %s and table_name = %s and data_type = 'super'",
            (schema, table),
        )
        return frozenset(str(r[0]) for r in cur.fetchall())


def _differs(column: str, is_super: bool) -> sql.Composable:
    """``s.c`` and ``t.c`` differ, null-safely (Redshift has no ``IS DISTINCT FROM``)."""
    c = sql.Identifier(column)
    if is_super:  # SUPER compares through its canonical JSON text
        left = sql.SQL("json_serialize(s.{})").format(c)
        right = sql.SQL("json_serialize(t.{})").format(c)
    else:
        left, right = sql.SQL("s.{}").format(c), sql.SQL("t.{}").format(c)
    return sql.SQL("({l} <> {r} or ({l} is null) <> ({r} is null))").format(l=left, r=right)


def _record(columns: Sequence[str], row: tuple[Any, ...], supers: frozenset[str]) -> dict[str, Any]:
    """One row as a JSON object ``COPY … FORMAT JSON 'auto'`` loads exactly."""
    return {c: _json_value(v, c in supers) for c, v in zip(columns, row, strict=True)}


def _json_value(value: Any, is_super: bool) -> Any:
    if value is None or (isinstance(value, bool | int | str) and not is_super):
        return value
    if is_super:  # the canonical JSON text the loaders produce, embedded as an object
        return json.loads(value) if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return str(value)  # a JSON string, so COPY keeps every digit
    if isinstance(value, datetime):
        if value.tzinfo is None:
            msg = f"naive datetime {value!r} (W3.1)"
            raise ValueError(msg)
        return value.isoformat()
    if isinstance(value, date | uuid.UUID):
        return str(value)
    msg = f"no JSON form for {type(value).__name__}"
    raise TypeError(msg)
