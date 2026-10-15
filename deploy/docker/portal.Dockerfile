# Image for the portal (upload page). Build context is the repo root. Same package as the others.
# /inbox belongs to group 10010 (mode 2775) in this image and in the mdeis-mcp image, so the volume
# is writable by the portal and readable by ingestd whichever container mounts it first.
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
    && groupadd --system --gid 10010 inbox \
    && useradd --system --uid 10003 --no-create-home --shell /usr/sbin/nologin --groups inbox portal \
    && mkdir /inbox && chown root:inbox /inbox && chmod 2775 /inbox
USER portal
EXPOSE 8080
CMD ["uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8080"]
