"""The code version recorded on every persisted result (CONTRIBUTING.md W4.3)."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from northstar.common.exceptions import ConfigurationError


def code_version(environ: Mapping[str, str] | None = None, repo: Path | None = None) -> str:
    """Return ``NBI_CODE_VERSION`` if set, else the Git commit, with ``-dirty`` for local edits.

    Images without Git (Airflow) set ``NBI_CODE_VERSION`` at build time.

    Raises:
        ConfigurationError: Neither the variable nor a Git checkout is available.
    """
    env = os.environ if environ is None else environ
    if version := env.get("NBI_CODE_VERSION"):
        return version
    cwd = repo or Path.cwd()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git is resolved from PATH on purpose
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        msg = "cannot determine the code version: set NBI_CODE_VERSION or run inside Git"
        raise ConfigurationError(msg) from error
    return f"{commit}-dirty" if dirty else commit
