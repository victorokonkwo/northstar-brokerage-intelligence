"""Load versioned policy YAML into validated domain models.

The domain layer never reads files (CONTRIBUTING.md W5). Callers load policy
here and pass the resulting objects into domain functions.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from northstar.common.exceptions import ConfigurationError


def load_policy[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    """Parse the YAML file at ``path`` and validate it as ``model``.

    YAML dates (``2026-01-01``) are parsed by ``yaml.safe_load`` as
    ``datetime.date`` values. No custom tags or Python objects are allowed.

    Raises:
        ConfigurationError: The file is missing, is not valid YAML, does not
            contain a mapping, or fails validation. The message names the file.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        msg = f"policy file not found: {path}"
        raise ConfigurationError(msg) from error
    except yaml.YAMLError as error:
        msg = f"policy file is not valid YAML: {path}: {error}"
        raise ConfigurationError(msg) from error
    if not isinstance(raw, dict):
        msg = f"policy file must contain a mapping at the top level: {path}"
        raise ConfigurationError(msg)
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        msg = f"invalid policy file {path}:\n{error}"
        raise ConfigurationError(msg) from error
