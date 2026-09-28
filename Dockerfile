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
RUN uv venv /opt/venv && uv sync --active --frozen --no-cache --no-dev
COPY tradebuddy /app/tradebuddy

EXPOSE 8080
# HOST=0.0.0.0 inside a container needs API_TOKEN; publish the port on 127.0.0.1 only.
ENV HOST=0.0.0.0
CMD ["python", "-m", "tradebuddy"]
