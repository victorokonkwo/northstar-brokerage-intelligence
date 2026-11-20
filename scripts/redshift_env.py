"""Print shell ``export`` lines for the cloud warehouse, from the Terraform data stack.

The endpoint, bucket, and secret ARN contain the AWS account ID, so they are
read from ``terraform output`` when needed and never committed. No secret is
printed: the admin password stays in Secrets Manager, and the pipeline uses
temporary IAM credentials.

Usage: ``eval "$(python scripts/redshift_env.py)"`` from the repository root.
"""

import json
import shlex
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

STACK = "infrastructure/terraform/data"


def exports(outputs: Mapping[str, Any]) -> list[str]:
    """The ``export`` lines for the ``NBI_RS_*`` variables, from ``terraform output -json``.

    Raises:
        KeyError: An expected output is missing (the stack isn't applied).
    """
    endpoint = outputs["redshift_endpoint"]["value"]
    values = {
        "NBI_WAREHOUSE": "redshift",
        "NBI_RS_HOST": str(endpoint["host"]),
        "NBI_RS_PORT": str(endpoint["port"]),
        "NBI_RS_DATABASE": "northstar",
        "NBI_RS_WORKGROUP": "northstar",
        "NBI_AWS_REGION": "ca-central-1",
        "NBI_BRONZE_BUCKET": str(outputs["bronze_bucket_name"]["value"]),
        "NBI_RS_ADMIN_SECRET_ARN": str(outputs["redshift_admin_secret_arn"]["value"]),
    }
    return [f"export {name}={shlex.quote(value)}" for name, value in values.items()]


def main() -> int:
    """Read the stack's outputs and print the exports."""
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["terraform", f"-chdir={STACK}", "output", "-json"],  # noqa: S607 - terraform on PATH
        check=True,
        capture_output=True,
        text=True,
    )
    print("\n".join(exports(json.loads(result.stdout))))  # noqa: T201 - the script's output
    return 0


if __name__ == "__main__":
    sys.exit(main())
