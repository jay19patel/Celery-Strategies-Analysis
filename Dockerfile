FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

WORKDIR /app

# System deps
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project dependency files
COPY pyproject.toml uv.lock /app/

# Install Python dependencies into /opt/venv (outside of /app so volumes don't shadow it)
RUN uv venv /opt/venv && uv sync --active --frozen --no-cache

# Copy rest of project
COPY . /app

EXPOSE 8080 8000 5555

# Default command
CMD ["python", "-c", "print('Image ready')"]
