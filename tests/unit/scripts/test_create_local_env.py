"""Tests for ``scripts/create_local_env.py``."""

import base64
import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "create_local_env", REPO_ROOT / "scripts" / "create_local_env.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


create_local_env = _load_script()


def _parse(content: str) -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in content.splitlines()
        if "=" in line and not line.startswith("#")
    )
    return dict(pairs)


def test_placeholders_become_distinct_random_secrets() -> None:
    template = "A=<generate>\nB=<generate>\nPLAIN=keep-me\n"
    first = _parse(create_local_env.render(template))
    second = _parse(create_local_env.render(template))
    assert first["PLAIN"] == "keep-me"
    assert first["A"] != first["B"]
    assert first["A"] != second["A"]
    assert len(first["A"]) >= 32  # 32 random bytes, base64-encoded


def test_fernet_placeholder_is_32_bytes_of_urlsafe_base64() -> None:
    rendered = _parse(create_local_env.render("KEY=<generate-fernet-key>\n"))
    assert len(base64.urlsafe_b64decode(rendered["KEY"])) == 32


def test_comments_and_blank_lines_are_copied_unchanged() -> None:
    template = "# NOTE=<generate>\n\nX=1\n"
    assert create_local_env.render(template) == template


def test_unknown_placeholder_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        create_local_env.render("X=<generate-typo>\n")


def test_every_placeholder_in_the_committed_template_is_known() -> None:
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    values = _parse(create_local_env.render(template)).values()
    assert not [v for v in values if v.startswith("<generate")]


def test_creates_owner_only_file_and_never_overwrites(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.write_text("SECRET=<generate>\n", encoding="utf-8")
    target = tmp_path / ".env"

    assert create_local_env.create_env_file(template, target) is True
    first = target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    assert create_local_env.create_env_file(template, target) is False
    assert target.read_text(encoding="utf-8") == first


def test_redshift_passwords_meet_its_rules() -> None:
    for _ in range(200):
        password = create_local_env.redshift_password()
        assert 8 <= len(password) <= 64
        assert any(c.isupper() for c in password)
        assert any(c.islower() for c in password)
        assert any(c.isdigit() for c in password)
        assert password.isalnum()  # no quote, backslash, slash, @, or space


def test_missing_keys_are_appended_and_existing_lines_kept(tmp_path: Path) -> None:
    template = tmp_path / "template"
    target = tmp_path / ".env"
    template.write_text("A=<generate>\n# note\nB=plain\nC=<generate-redshift-password>\n")
    target.write_text("A=in-use-secret\nOWN=mine", encoding="utf-8")  # no final newline

    assert create_local_env.add_missing_keys(template, target) == ["B", "C"]
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["A=in-use-secret", "OWN=mine"]
    values = _parse("\n".join(lines))
    assert values["B"] == "plain"
    assert values["C"].isalnum()
    assert create_local_env.add_missing_keys(template, target) == []
