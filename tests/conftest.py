"""Shared pytest configuration.

Hypothesis profiles (select with the HYPOTHESIS_PROFILE environment variable):

- ``dev`` (default): random examples, so local runs keep exploring new inputs.
- ``ci``: derandomized, so a CI run is reproducible from the commit alone
  (CONTRIBUTING.md W4).
"""

import os

from hypothesis import settings

settings.register_profile("dev")
settings.register_profile("ci", derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
