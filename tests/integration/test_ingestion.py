"""Ingestion into SeaweedFS and loading into Postgres (needs `make up`).

Each test uses its own key prefix in the bucket and throwaway schemas, and
removes both afterwards.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import psycopg
import pytest
from botocore.config import Config  # type: ignore[import-untyped]
from psycopg import sql

from northstar.application.ingest import ingest_dataset, load_run
from northstar.application.simulate import simulate_dataset
from northstar.infrastructure.object_store import ObjectStoreSettings, S3ObjectStore
from northstar.infrastructure.postgres import PostgresSettings, WarehouseSchemas, ensure_tables
from northstar.ingestion.pipeline import QuarantineThresholdError

from factories import CONFIG

pytestmark = pytest.mark.integration


@pytest.fixture
def store() -> Iterator[S3ObjectStore]:
    settings = ObjectStoreSettings.from_env()
    prefix = f"it-{uuid.uuid4().hex[:12]}/"
    yield S3ObjectStore(settings, prefix)
    client = boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=settings.bucket, Prefix=prefix
    ):
        for item in page.get("Contents", []):
            client.delete_object(Bucket=settings.bucket, Key=item["Key"])


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        yield connection


@pytest.fixture
def schemas(conn: psycopg.Connection[Any]) -> Iterator[WarehouseSchemas]:
    suffix = uuid.uuid4().hex[:12]
    names = WarehouseSchemas(raw=f"it_raw_{suffix}", ledger=f"it_ledger_{suffix}")
    ensure_tables(conn, names)
    conn.commit()
    yield names
    conn.rollback()
    with conn.cursor() as cur:
        for name in (names.raw, names.ledger):
            cur.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))
    conn.commit()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return simulate_dataset(
        CONFIG,
        CONFIG / "simulation" / "datasets" / "dev.yaml",
        42,
        tmp_path_factory.mktemp("generated"),
        CONFIG / "simulation" / "defects" / "dev.yaml",
    ).root


def test_the_store_never_overwrites_an_object(store: S3ObjectStore) -> None:
    assert store.put_if_absent("raw/x/part-1.jsonl", b"first\n")
    assert not store.put_if_absent("raw/x/part-1.jsonl", b"second\n")
    assert store.get("raw/x/part-1.jsonl") == b"first\n"


def _table_digest(
    conn: psycopg.Connection[Any], schema: str, table: str, key: str
) -> tuple[int, str]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select count(*), md5(coalesce(string_agg(t::text, '|' order by {key}), '')) "
                "from (select * from {table}) t"
            ).format(key=sql.Identifier(key), table=sql.Identifier(schema, table))
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0]), str(row[1])


def test_ingest_load_and_replay_are_idempotent(
    store: S3ObjectStore,
    conn: psycopg.Connection[Any],
    schemas: WarehouseSchemas,
    dataset: Path,
) -> None:
    first = ingest_dataset(store, CONFIG, dataset, "it-first", "test-sha")
    assert all(o.written for o in first.objects)
    loaded = load_run(conn, store, CONFIG, "it-first", schemas)
    lines = {Path(f.path).name: f.lines for f in first.files}
    assert loaded.events_read == loaded.events_inserted == lines["events.jsonl"]
    assert (
        loaded.records_read
        == loaded.records_inserted
        == sum(n for name, n in lines.items() if name != "events.jsonl")
    )
    assert loaded.lines_inserted > 0

    tables = (
        (schemas.raw, "brokerage_events", "event_id"),
        (schemas.raw, "external_records", "record_id"),
        (schemas.ledger, "journal_lines", "journal_line_id"),
    )
    before = {t: _table_digest(conn, *t) for t in tables}

    replay = ingest_dataset(store, CONFIG, dataset, "it-replay", "test-sha")
    assert not any(o.written for o in replay.objects)
    again = load_run(conn, store, CONFIG, "it-replay", schemas)
    assert (again.events_inserted, again.lines_inserted, again.records_inserted) == (0, 0, 0)
    assert {t: _table_digest(conn, *t) for t in tables} == before


def test_a_failed_run_cannot_be_loaded(
    store: S3ObjectStore,
    conn: psycopg.Connection[Any],
    schemas: WarehouseSchemas,
    tmp_path: Path,
) -> None:
    root = tmp_path / "bad"
    (root / "canonical").mkdir(parents=True)
    (root / "canonical" / "events.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(QuarantineThresholdError, match="over the"):
        ingest_dataset(store, CONFIG, root, "it-bad", "test-sha")
    with pytest.raises(ValueError, match="did not succeed"):
        load_run(conn, store, CONFIG, "it-bad", schemas)
