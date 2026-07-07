FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ app/
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY *.py .

ENV PATH="/app/.venv/bin:$PATH"