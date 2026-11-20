"""Warehouse dialects: PostgreSQL (local and CI) and Amazon Redshift (cloud).

Most of the warehouse SQL is shared. The differences that matter here are
recorded in docs/sources.md:

- Redshift has no triggers and no CHECK constraints, and does not enforce
  primary or unique keys. Append-only is enforced with grants instead (the
  pipeline's database user owns nothing and holds INSERT and SELECT only), and
  each CHECK is repeated as a dbt test (owner decision 2026-11-20).
- Redshift has no ``uuid`` or ``jsonb`` type, and turns ``text`` into
  ``varchar(256)``. Too-long values are refused, not truncated.
- Redshift has no ``COPY FROM STDIN`` and no ``ON CONFLICT``. Rows are staged
  in S3 and loaded with ``COPY`` (see ``redshift.py``).

The Postgres DDL in this package stays the source of truth: ``redshift_ddl``
translates it, so the two can't drift apart.
"""

import re
from enum import StrEnum
from typing import Any, Final

import psycopg
from psycopg.adapt import Dumper
from psycopg.rows import TupleRow

REDSHIFT_TEXT: Final = "varchar(4096)"
"""Redshift type for the package's ``text`` columns. Longer values are refused."""

_TYPES: Final = (
    (re.compile(r"(?<=\s)uuid(?=[\s,)]|$)", re.MULTILINE), "char(36)"),
    (re.compile(r"(?<=\s)jsonb(?=[\s,)]|$)", re.MULTILINE), "super"),
    (re.compile(r"(?<=\s)text(?=[\s,)]|$)", re.MULTILINE), REDSHIFT_TEXT),
    (re.compile(r"\bdefault now\(\)"), "default getdate()"),
)
_CHECK: Final = re.compile(r"\bcheck\s*\(", re.IGNORECASE)
_COMMENT: Final = re.compile(r"--[^\n]*")


class Dialect(StrEnum):
    """The SQL dialect of a warehouse connection."""

    POSTGRES = "postgres"
    REDSHIFT = "redshift"


class UuidAsText(Dumper):
    """Send a ``uuid.UUID`` parameter as text.

    psycopg sends it as Postgres's ``uuid`` type (OID 2950), which Redshift
    doesn't have ("cache lookup failed for type 2950"); Redshift keeps these
    IDs as ``char(36)``.
    """

    oid = psycopg.postgres.types["text"].oid

    def dump(self, obj: Any) -> bytes:
        """The UUID's canonical string, e.g. ``"b282d7dd-461c-56b6-88e9-fdda7f98dc56"``."""
        return str(obj).encode()


class RedshiftConnection(psycopg.Connection[TupleRow]):
    """A psycopg connection to Redshift Serverless.

    It carries what a load needs beyond the connection itself: the S3 stage
    that ``COPY`` reads from, and the IAM role it reads with.
    """

    stage: Any = None
    """The ``redshift.Stage`` for loads; set by ``RedshiftSettings.connect``."""
    admin: bool = False
    """Whether this is the namespace admin, the only user that creates tables."""


def dialect_of(conn: psycopg.Connection[Any]) -> Dialect:
    """The dialect of ``conn``: Redshift for a ``RedshiftConnection``, else Postgres."""
    return Dialect.REDSHIFT if isinstance(conn, RedshiftConnection) else Dialect.POSTGRES


def redshift_ddl(ddl: str) -> str:
    """Translate one of this package's Postgres ``create table`` statements to Redshift.

    CHECK constraints are dropped (dbt tests repeat them), and ``uuid``,
    ``jsonb``, ``text``, and ``default now()`` are mapped. Everything else,
    including primary, unique, and foreign keys (informational on Redshift),
    is kept as written.

    Raises:
        ValueError: A CHECK constraint's parentheses don't balance.
    """
    out = _drop_checks(_COMMENT.sub("", ddl))
    for pattern, replacement in _TYPES:
        out = pattern.sub(replacement, out)
    return out


def _drop_checks(ddl: str) -> str:
    """Remove every ``check (...)`` and repair the commas around it."""
    out, i = [], 0
    while (m := _CHECK.search(ddl, i)) is not None:
        out.append(ddl[i : m.start()])
        depth, j = 0, m.end() - 1
        while j < len(ddl):
            depth += {"(": 1, ")": -1}.get(ddl[j], 0)
            if depth == 0:
                break
            j += 1
        if depth != 0:
            msg = f"unbalanced CHECK constraint at offset {m.start()}"
            raise ValueError(msg)
        i = j + 1
    out.append(ddl[i:])
    text = "".join(out)
    # A comma left alone on its line belongs to the line above (a column
    # whose CHECK was on a continuation line).
    text = re.sub(r"[ \t]*\n[ \t]*,", ",", text)
    text = re.sub(r",(\s*,)+", ",", text)  # a whole-line constraint between others
    text = re.sub(r",\s*\n(\s*)\)", r"\n\1)", text)  # a constraint that was last
    text = re.sub(r"[ \t]+,", ",", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n\s*\n", "\n", text)
