# Image for the orchestrator. Build context is the repo root. The `orchestrator` extra adds
# LangGraph and its Postgres checkpointer, which no other image carries.
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[orchestrator]" \
    && useradd --system --uid 10004 --no-create-home --shell /usr/sbin/nologin orchestrator
USER orchestrator
EXPOSE 8300
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8300"]
