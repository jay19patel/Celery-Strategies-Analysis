FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# System deps (optional; keep minimal)
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files
COPY pyproject.toml uv.lock /app/

# Install Python dependencies using uv
RUN uv sync --frozen --no-cache

# Copy rest of project
COPY . /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

EXPOSE 8000 5555

# Default command does nothing; each service sets its own command in docker-compose
CMD ["python", "-c", "print('Image ready')"]


