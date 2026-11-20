"""The Redshift dialect: DDL translation, null-safe comparison, and load encoding."""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from northstar.infrastructure import postgres, recon_store, snapshots
from northstar.infrastructure.dialect import REDSHIFT_TEXT, UuidAsText, redshift_ddl
from northstar.infrastructure.loading import LoadConflictError, distinct_rows
from northstar.infrastructure.redshift import _differs, _json_value, _record

ALL_DDL = {
    f"{module.__name__}.{name}": getattr(module, name)
    for module in (postgres, recon_store, snapshots)
    for name in dir(module)
    if name.endswith("_DDL") and name not in {"_APPEND_ONLY_DDL", "_TIMELINE_DDL"}
}


@pytest.mark.parametrize("name", sorted(ALL_DDL))
def test_every_table_translates_without_checks_or_postgres_only_types(name: str) -> None:
    out = redshift_ddl(ALL_DDL[name]).lower()
    assert "check" not in out
    for word in (" uuid", " jsonb", " text", "now()"):
        assert word not in out, f"{name} still has {word!r}"
    assert out.count("(") == out.count(")")
    assert ",\n)" not in out.replace(" ", "")


def test_columns_and_keys_survive_the_translation() -> None:
    out = redshift_ddl(recon_store._TRANSITIONS_DDL)
    for column in ("transition_id", "exception_id", "sequence", "to_status", "resolution_type"):
        assert column in out
    assert "unique (exception_id, sequence)" in out  # informational on Redshift, kept
    assert "references {exceptions}" in out
    assert f"to_status       {REDSHIFT_TEXT}        not null," in out  # comma repaired


def test_unbalanced_check_is_refused() -> None:
    with pytest.raises(ValueError, match="unbalanced"):
        redshift_ddl("create table t (a integer check (a > (1)")


def test_differs_is_null_safe() -> None:
    rendered = _differs("amount", is_super=False).as_string(None)
    assert rendered == (
        '(s."amount" <> t."amount" or (s."amount" is null) <> (t."amount" is null))'
    )
    on_super = _differs("payload", is_super=True).as_string(None)
    assert 'json_serialize(s."payload") <> json_serialize(t."payload")' in on_super
    assert isinstance(_differs("x", is_super=False), sql.Composable)


def test_values_encode_exactly_for_copy() -> None:
    assert _json_value(Decimal("1234567890123456.78"), is_super=False) == "1234567890123456.78"
    assert _json_value(date(2026, 3, 2), is_super=False) == "2026-03-02"
    when = datetime(2026, 3, 2, 14, 30, 0, 123456, tzinfo=UTC)
    assert _json_value(when, is_super=False) == "2026-03-02T14:30:00.123456+00:00"
    assert _json_value(None, is_super=True) is None
    assert _json_value(True, is_super=False) is True
    the_id = uuid.uuid5(uuid.NAMESPACE_URL, "x")
    assert _json_value(the_id, is_super=False) == str(the_id)
    assert _json_value('{"b":1,"a":"2.50"}', is_super=True) == {"a": "2.50", "b": 1}


def test_naive_datetimes_and_unknown_types_are_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        _json_value(datetime(2026, 3, 2, 14, 30), is_super=False)  # noqa: DTZ001 - the point
    with pytest.raises(TypeError, match="no JSON form"):
        _json_value(1.5, is_super=False)


def test_a_record_round_trips_through_json() -> None:
    record = _record(
        ("event_id", "amount", "payload"),
        ("e1", Decimal("10.00"), '{"k":"v"}'),
        frozenset({"payload"}),
    )
    assert json.loads(json.dumps(record)) == {
        "event_id": "e1",
        "amount": "10.00",
        "payload": {"k": "v"},
    }


def test_distinct_rows_keeps_one_per_key_and_refuses_disagreement() -> None:
    rows = [("k1", "a", "run1"), ("k1", "a", "run2"), ("k2", "b", "run1")]
    assert distinct_rows("s", "t", "key", rows) == [("k1", "a", "run1"), ("k2", "b", "run1")]
    with pytest.raises(LoadConflictError, match="repeats key k1"):
        distinct_rows("s", "t", "key", [("k1", "a", "r"), ("k1", "b", "r")])


def test_uuids_are_sent_to_redshift_as_their_text() -> None:
    value = uuid.uuid5(uuid.NAMESPACE_URL, "x")
    dumper = UuidAsText(uuid.UUID)
    assert dumper.dump(value) == str(value).encode()
    assert dumper.oid == psycopg.postgres.types["text"].oid  # not uuid (2950)
