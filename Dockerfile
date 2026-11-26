# syntax=docker/dockerfile:1

# ---- Stage 1: install Defuddle with npm, keep only what we need ----
FROM node:26-trixie-slim AS node-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# ---- Stage 2: the image that actually runs ----
FROM python:3.13-slim-trixie

# The node binary links against libatomic, which slim Debian leaves out
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 \
 && rm -rf /var/lib/apt/lists/*

# Node.js runtime only (no npm), from stage 1
COPY --from=node-deps /usr/local/bin/node /usr/local/bin/node
# uv, pinned to the version that wrote uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/app/node_modules/.bin:$PATH" \
    HOME=/app/data/home

WORKDIR /app

# Dependencies first: this layer is reused until pyproject.toml or uv.lock change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --from=node-deps /app/node_modules ./node_modules

# Then the code (filtered by .dockerignore), and install the project itself
COPY . .
RUN uv sync --frozen

# SQLite lives here; mount a volume at this path
VOLUME /app/data

CMD ["sh", "/app/start.sh"]
