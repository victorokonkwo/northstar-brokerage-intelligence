# Airflow 3.4.0 with the northstar project in its own virtualenv (owner
# decision 2026-11-19). Tasks call /opt/northstar/.venv/bin/northstar and
# dbt-ol, so the project's locked dependencies never mix with Airflow's.
# Build context: the repository root (see compose.yaml).
FROM apache/airflow:3.4.0-python3.13

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

# The image's own interpreter; uv must not download another one.
ENV UV_PYTHON=/usr/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/northstar/.venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

USER root
RUN mkdir -p /opt/northstar && chown airflow:root /opt/northstar
USER airflow
WORKDIR /opt/northstar

# Dependencies first (cached until the lock file changes), then the project.
COPY --chown=airflow:root pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --group dbt --no-install-project
COPY --chown=airflow:root src ./src
RUN uv sync --locked --no-dev --group dbt --no-editable
COPY --chown=airflow:root config ./config
COPY --chown=airflow:root warehouse/dbt ./warehouse/dbt

# The image has no Git checkout, so the code version is fixed at build time (W4.3).
ARG NBI_CODE_VERSION
RUN test -n "${NBI_CODE_VERSION}" || { echo "NBI_CODE_VERSION build arg is required" >&2; exit 1; }
ENV NBI_CODE_VERSION=${NBI_CODE_VERSION}

WORKDIR /opt/airflow
