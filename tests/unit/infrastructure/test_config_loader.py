"""Tests for ``northstar.infrastructure.config_loader``."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from northstar.common.exceptions import ConfigurationError
from northstar.infrastructure.config_loader import load_policy


class _Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    rate: int


def test_valid_file_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text('version: "1"\nrate: 5\n', encoding="utf-8")
    assert load_policy(path, _Policy) == _Policy(version="1", rate=5)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("version: [unclosed\n", "not valid YAML"),
        ("- a list\n", "mapping at the top level"),
        ('version: "1"\n', "invalid policy file"),
        ('version: "1"\nrate: 5\nextra: 1\n', "invalid policy file"),
        ("!!python/object:os.system {}\n", "not valid YAML"),
    ],
)
def test_invalid_files_name_the_file(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message) as excinfo:
        load_policy(path, _Policy)
    assert str(path) in str(excinfo.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_policy(tmp_path / "absent.yaml", _Policy)
