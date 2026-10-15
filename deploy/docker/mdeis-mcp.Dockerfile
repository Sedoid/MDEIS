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
# /inbox is shared with the portal image: group 10010, mode 2775 (see portal.Dockerfile).
# ingestd, which runs from this image, only reads it.
RUN pip install . \
    && groupadd --system --gid 10010 inbox \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin --groups inbox mdeis \
    && mkdir /inbox && chown root:inbox /inbox && chmod 2775 /inbox
USER mdeis
EXPOSE 8100
CMD ["python", "-m", "mdeis.server"]
