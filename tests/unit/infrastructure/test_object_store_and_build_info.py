"""Object store settings and the recorded code version (W4.3)."""

import subprocess
from pathlib import Path

import pytest

from northstar.common.exceptions import ConfigurationError
from northstar.infrastructure.build_info import code_version
from northstar.infrastructure.object_store import DEFAULT_BUCKET, ObjectStoreSettings

S3_ENV = {
    "NBI_S3_ENDPOINT_URL": "http://localhost:8333",
    "NBI_S3_ACCESS_KEY_ID": "key",
    "NBI_S3_SECRET_ACCESS_KEY": "secret",
}


def test_settings_default_to_the_local_bucket() -> None:
    assert ObjectStoreSettings.from_env(S3_ENV).bucket == DEFAULT_BUCKET
    assert ObjectStoreSettings.from_env(S3_ENV | {"NBI_S3_BUCKET": "other"}).bucket == "other"


def test_missing_settings_are_named_without_values() -> None:
    with pytest.raises(ConfigurationError) as error:
        ObjectStoreSettings.from_env({k: v for k, v in S3_ENV.items() if "SECRET" not in k})
    assert str(error.value) == (
        "missing environment variables: NBI_S3_SECRET_ACCESS_KEY (run `make env`)"
    )


def test_code_version_prefers_the_environment() -> None:
    assert code_version({"NBI_CODE_VERSION": "abc123"}) == "abc123"


def test_code_version_reads_git_and_marks_local_edits(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    git("add", "f.txt")
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "c")
    head = git("rev-parse", "HEAD")
    assert code_version({}, tmp_path) == head
    (tmp_path / "f.txt").write_text("b", encoding="utf-8")
    assert code_version({}, tmp_path) == f"{head}-dirty"


def test_code_version_without_git_or_variable_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="NBI_CODE_VERSION"):
        code_version({}, tmp_path)
