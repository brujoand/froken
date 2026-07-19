# Single stage: there is no build step to isolate. No JS toolchain, no compiled
# assets, and the curriculum is baked in as data.
FROM python:3.13-slim

# uv from the official image rather than pip-installed, so the version is pinned
# by digest rather than by whatever pip resolves at build time.
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies before source, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY data/curriculum/ ./data/curriculum/
COPY data/items/ ./data/items/
RUN uv sync --frozen --no-dev

# Non-root, and a fixed uid so a read-only root filesystem or a restrictive
# PodSecurityContext has something predictable to point at.
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin froken \
    && chown -R froken:froken /app
USER 65532

# The same version the image is TAGGED with, so the tag in the registry and the
# version the app reports come from one source.
ARG VERSION=dev
ENV APP_VERSION=${VERSION}

EXPOSE 8000

# No secrets, no network egress, no configuration required. If this container
# ever needs an API key, something has gone wrong upstream of here.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"

# The venv binary directly, not `uv run`: uv would re-check the lockfile at
# container start, which is a resolution step -- and potentially a network call
# -- on a path that should do nothing but exec the server.
CMD ["/app/.venv/bin/uvicorn", "froken.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
