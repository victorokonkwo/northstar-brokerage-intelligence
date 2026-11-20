"""The cloud warehouse's environment, from Terraform outputs."""

import importlib.util
import shlex
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "redshift_env.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("redshift_env", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OUTPUTS = {
    "redshift_endpoint": {"value": {"host": "wg.example.redshift-serverless.test", "port": 5439}},
    "bronze_bucket_name": {"value": "northstar-bronze-000000000000-ca-central-1"},
    "redshift_admin_secret_arn": {"value": "arn:aws:secretsmanager:ca-central-1:0:secret:x"},
}


def test_exports_name_every_variable_the_redshift_settings_read() -> None:
    lines = _module().exports(OUTPUTS)
    values = dict(shlex.split(line.removeprefix("export "))[0].split("=", 1) for line in lines)
    assert values["NBI_WAREHOUSE"] == "redshift"
    assert values["NBI_RS_HOST"] == "wg.example.redshift-serverless.test"
    assert values["NBI_RS_PORT"] == "5439"
    assert values["NBI_BRONZE_BUCKET"] == "northstar-bronze-000000000000-ca-central-1"
    assert values["NBI_RS_ADMIN_SECRET_ARN"].startswith("arn:aws:secretsmanager:")
    assert not any("password" in line.lower() for line in lines)


def test_a_missing_output_is_an_error() -> None:
    with pytest.raises(KeyError):
        _module().exports({"redshift_endpoint": OUTPUTS["redshift_endpoint"]})
