"""Create the local-only ``.env`` file from ``.env.example``.

Every ``<generate>`` value becomes a random URL-safe token, and every
``<generate-fernet-key>`` value becomes a Fernet key (32 random bytes,
URL-safe base64), the format Airflow requires for ``AIRFLOW__CORE__FERNET_KEY``.
Every ``<generate-redshift-password>`` value becomes 32 random letters and
digits with at least one upper-case letter, one lower-case letter, and one
digit (Redshift's password rules). All other lines are copied unchanged.

An existing ``.env`` is never overwritten, so secrets in use are never
replaced by accident. Keys the template has gained since are appended to it,
generated the same way, and every existing line is left as it is.

Usage: ``python scripts/create_local_env.py [TEMPLATE] [TARGET]``
(defaults: ``.env.example`` and ``.env``).
"""

import base64
import os
import secrets
import string
import sys
from collections.abc import Callable
from pathlib import Path

_ALPHANUMERIC = string.ascii_letters + string.digits


def redshift_password(length: int = 32) -> str:
    """Random letters and digits with each of upper case, lower case, and a digit.

    Redshift requires 8 to 64 characters with at least one of each; letters
    and digits avoid every character it refuses (quotes, backslash, slash, @,
    space).
    """
    while True:
        password = "".join(secrets.choice(_ALPHANUMERIC) for _ in range(length))
        if (
            any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password


GENERATORS: dict[str, Callable[[], str]] = {
    "<generate>": lambda: secrets.token_urlsafe(32),
    "<generate-fernet-key>": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "<generate-redshift-password>": redshift_password,
}


def render(template: str) -> str:
    """Return ``template`` with every generator placeholder value replaced.

    A placeholder is only recognised as the whole value of a ``KEY=VALUE``
    line. Comments and blank lines are copied as they are.

    Raises:
        ValueError: A value starts with ``<generate`` but is not a known
            placeholder, which would otherwise leak into ``.env`` as a literal.
    """
    lines = []
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value.startswith("<generate"):
            generator = GENERATORS.get(value)
            if generator is None:
                msg = f"unknown placeholder {value!r} for {key}"
                raise ValueError(msg)
            line = f"{key}={generator()}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def create_env_file(template_path: Path, target_path: Path) -> bool:
    """Write ``target_path`` from ``template_path`` unless it already exists.

    The file is created with owner-only permissions (0600).

    Returns:
        ``True`` if the file was written, ``False`` if it already existed.
    """
    if target_path.exists():
        return False
    content = render(template_path.read_text(encoding="utf-8"))
    fd = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as env_file:
        env_file.write(content)
    return True


def _keys(content: str) -> set[str]:
    return {
        line.partition("=")[0].strip()
        for line in content.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def add_missing_keys(template_path: Path, target_path: Path) -> list[str]:
    """Append to ``target_path`` the template's keys it lacks; return their names.

    Existing lines, including their secrets, are never changed or reordered.
    """
    current = target_path.read_text(encoding="utf-8")
    present = _keys(current)
    rendered = render(template_path.read_text(encoding="utf-8"))
    missing = [
        line
        for line in rendered.splitlines()
        if "=" in line
        and not line.lstrip().startswith("#")
        and line.partition("=")[0].strip() not in present
    ]
    if missing:
        separator = "" if current.endswith("\n") or not current else "\n"
        with target_path.open("a", encoding="utf-8") as env_file:
            env_file.write(separator + "\n".join(missing) + "\n")
    return [line.partition("=")[0].strip() for line in missing]


def main(argv: list[str]) -> int:
    """Command-line entry point; returns the process exit code."""
    template_path = Path(argv[1]) if len(argv) > 1 else Path(".env.example")
    target_path = Path(argv[2]) if len(argv) > 2 else Path(".env")
    if create_env_file(template_path, target_path):
        sys.stdout.write(f"created {target_path} with generated local secrets\n")
    elif added := add_missing_keys(template_path, target_path):
        sys.stdout.write(f"{target_path} already exists; added {', '.join(added)}\n")
    else:
        sys.stdout.write(f"{target_path} already exists; left unchanged\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
