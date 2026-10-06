FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TZ=UTC

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
COPY pyproject.toml uv.lock /app/
RUN uv venv /opt/venv && uv sync --active --frozen --no-cache --no-dev --extra ml
COPY tradebuddy /app/tradebuddy

EXPOSE 8080
# docker-compose passes the role: feed, engine, web or worker.
ENTRYPOINT ["python", "-m", "tradebuddy"]
