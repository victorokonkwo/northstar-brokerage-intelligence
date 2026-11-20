"""The dbt project stays portable between Postgres and Redshift (ADR 005).

CI builds dbt on Postgres only, so this guards the Redshift side statically:
every construct below compiles on Postgres but is missing on Redshift, or means
something else there (docs/sources.md). Use the macros in
``warehouse/dbt/macros/dialect.sql`` instead.
"""

import re
from pathlib import Path

import pytest

DBT = Path(__file__).parents[2] / "warehouse" / "dbt"
SQL = sorted([*DBT.glob("models/**/*.sql"), *DBT.glob("tests/**/*.sql")])
YAML = sorted(DBT.glob("models/**/*.yml"))

FORBIDDEN_SQL = {
    # Redshift reads bare `numeric` as numeric(18, 0) and rounds away decimals.
    "bare numeric": re.compile(r"(::|\bas\s+)numeric\b(?!\s*\()", re.IGNORECASE),
    # Redshift turns `text` into varchar(256); use string_type().
    "text type": re.compile(r"(::|\bas\s+)text\b", re.IGNORECASE),
    "jsonb operator": re.compile(r"->>|\s->\s"),
    "is distinct from": re.compile(r"\bis\s+(not\s+)?distinct\s+from\b", re.IGNORECASE),
    "aggregate filter": re.compile(r"\bfilter\s*\(\s*where\b", re.IGNORECASE),
    "distinct on": re.compile(r"\bdistinct\s+on\b", re.IGNORECASE),
    "generate_series": re.compile(r"\bgenerate_series\b", re.IGNORECASE),
    "jsonb function": re.compile(r"\bjsonb?_[a-z_]+\(", re.IGNORECASE),
    "lateral": re.compile(r"\blateral\b", re.IGNORECASE),
}
FORBIDDEN_YAML = re.compile(r"data_type:\s*(text|uuid|numeric|jsonb?)\s*[,}\n]", re.IGNORECASE)
# In a YAML flow mapping, an unquoted `numeric(38, 2)` splits at its comma.
UNQUOTED_COMMA_TYPE = re.compile(r"data_type:\s*[a-z]+\([^)\"']*,[^)]*\)", re.IGNORECASE)


def _strip(text: str) -> str:
    """Raw SQL only: no comments (prose may name these constructs) and no Jinja
    expressions (the portable macros render per dialect)."""
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    text = re.sub(r"\{\{.*?\}\}", "", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", text)


def _code(path: Path) -> str:
    return _strip(path.read_text(encoding="utf-8"))


def test_the_project_has_sql_to_check() -> None:
    assert len(SQL) > 40
    assert YAML


@pytest.mark.parametrize("path", SQL, ids=lambda p: str(p.relative_to(DBT)))
def test_sql_uses_only_portable_constructs(path: Path) -> None:
    code = _code(path)
    found = [name for name, pattern in FORBIDDEN_SQL.items() if pattern.search(code)]
    assert not found, f"{path.relative_to(DBT)} uses {found}; see macros/dialect.sql"


@pytest.mark.parametrize("path", YAML, ids=lambda p: str(p.relative_to(DBT)))
def test_contract_types_are_portable(path: Path) -> None:
    text = path.read_text(encoding="utf-8") + "\n"
    bad = FORBIDDEN_YAML.findall(text)
    assert not bad, f"{path.relative_to(DBT)}: contract types {bad} differ on Redshift"
    split = UNQUOTED_COMMA_TYPE.findall(text)
    assert not split, f"{path.relative_to(DBT)}: quote {split}: YAML splits it at the comma"


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("select sum(a)::numeric as x", "bare numeric"),
        ("select a::text", "text type"),
        ("select payload ->> 'k'", "jsonb operator"),
        ("where a is distinct from b", "is distinct from"),
        ("count(*) filter (where x)", "aggregate filter"),
        ("jsonb_array_elements_text(a)", "jsonb function"),
    ],
)
def test_the_guard_catches_each_construct(snippet: str, name: str) -> None:
    assert FORBIDDEN_SQL[name].search(snippet)


def test_the_guard_allows_the_portable_forms() -> None:
    portable = "select x::numeric(38, 2), cast(y as numeric(18, 6)), {{ json_text('p', 'k') }}"
    assert not any(p.search(_strip(portable)) for p in FORBIDDEN_SQL.values())
    assert not FORBIDDEN_YAML.search('data_type: "numeric(38, 2)"}')
    assert UNQUOTED_COMMA_TYPE.search("{name: x, data_type: numeric(38, 2), constraints: []}")
    assert not UNQUOTED_COMMA_TYPE.search('{name: x, data_type: "numeric(38, 2)"}')
