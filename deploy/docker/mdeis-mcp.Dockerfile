# Image for mdeis-mcp, the migration step and the test runner. Build context is the repo root.
#   target runtime  the server (no dev tools, non-root)
#   target dev      pytest and ruff, used by `make test` and `make lint`
# Layer order: only pyproject.toml is copied before the dependencies are installed, so a change under
# src/ reuses the cached dependency layers and only "copy src" and "install the package, --no-deps"
# run again. Dependencies download again only when pyproject.toml changes.
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
# The runtime dependencies, read from pyproject.toml.
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

FROM base AS dev
# The dev extra (pytest, ruff, fpdf2, ...) and the build tool, read from pyproject.toml. The tool is
# installed here so the editable install below needs no download (--no-build-isolation).
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['build-system']['requires'] + p['project']['optional-dependencies']['dev']))" > /tmp/requirements-dev.txt \
    && pip install -r /tmp/requirements-dev.txt \
    && rm /tmp/requirements-dev.txt
COPY README.md LICENSE ./
COPY src ./src
RUN pip install -e . --no-deps --no-build-isolation
CMD ["pytest"]

FROM base AS runtime
COPY README.md LICENSE ./
COPY src ./src
# /inbox is shared with the portal image: group 10010, mode 2775 (see portal.Dockerfile).
# ingestd, which runs from this image, only reads it.
# /run/measure-runner holds the Unix socket of the measure runner (same image, same user id 10001,
# so mdeis-mcp and the runner can both use it). Docker copies this owner into a new named volume.
# Build isolation stays on here, so the runtime image gets no build tool (one small download).
RUN pip install . --no-deps \
    && groupadd --system --gid 10010 inbox \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin --groups inbox mdeis \
    && mkdir /inbox && chown root:inbox /inbox && chmod 2775 /inbox \
    && mkdir /run/measure-runner && chown mdeis:mdeis /run/measure-runner && chmod 0770 /run/measure-runner
USER mdeis
EXPOSE 8100
CMD ["python", "-m", "mdeis.server"]
