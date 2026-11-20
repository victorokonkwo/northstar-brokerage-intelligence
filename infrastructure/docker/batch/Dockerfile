# The cloud batch image (roadmap 4G): the northstar project and dbt, run as one
# Fargate task per daily-close step (orchestration/cloud/run_step.sh).
# linux/arm64, which Fargate runs on Graviton (ADR 010).
# Build context: the repository root (make batch-image).
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/northstar/.venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH=/opt/northstar/.venv/bin:$PATH

RUN useradd --create-home --uid 10001 northstar \
    && mkdir -p /opt/northstar && chown northstar /opt/northstar
USER northstar
WORKDIR /opt/northstar

# Dependencies first (cached until the lock file changes), then the project.
COPY --chown=northstar pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --group dbt --no-install-project
COPY --chown=northstar src ./src
RUN uv sync --locked --no-dev --group dbt --no-editable
COPY --chown=northstar config ./config
COPY --chown=northstar warehouse/dbt ./warehouse/dbt
COPY --chown=northstar orchestration/cloud/run_step.sh /usr/local/bin/run-step

# The image has no Git checkout, so the code version is fixed at build time (W4.3).
ARG NBI_CODE_VERSION
RUN test -n "${NBI_CODE_VERSION}" || { echo "NBI_CODE_VERSION build arg is required" >&2; exit 1; }
ENV NBI_CODE_VERSION=${NBI_CODE_VERSION}

ENTRYPOINT ["run-step"]
