# Image for mdeis-mcp, the migration step and the test runner. Build context is the repo root.
#   target runtime  the server (no dev tools, non-root)
#   target dev      pytest and ruff, used by `make test` and `make lint`
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

FROM base AS dev
RUN pip install -e ".[dev]"
CMD ["pytest"]

FROM base AS runtime
RUN pip install . \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mdeis
USER mdeis
EXPOSE 8100
CMD ["python", "-m", "mdeis.server"]
