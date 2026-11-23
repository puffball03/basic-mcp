# syntax=docker/dockerfile:1
# Militech equipment request MCP server + agent — one image, three commands
# (server | demo | test). See docs/requirements.md §10.

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app

# --- dependency layer: only re-runs when pyproject/uv.lock change ---
FROM base AS deps
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# --- app layer ---
FROM deps AS app
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen \
 && chmod +x docker/entrypoint.sh \
 && useradd --create-home --uid 1000 militech \
 && mkdir -p /app/traces /app/data \
 && chown -R militech:militech /app

ENV PATH="/app/.venv/bin:$PATH" \
    MILITECH_DATA_DIR=/app/data
USER militech

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["test"]
