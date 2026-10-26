# One image for api, orchestrator and workers (stateless; role chosen by command/env).
FROM ghcr.io/astral-sh/uv:0.9.8 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
CMD ["uvicorn", "provcheck.services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
