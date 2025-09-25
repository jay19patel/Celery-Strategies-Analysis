FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (optional; keep minimal)
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a venv at /.venv to match local usage
COPY requirements.txt /app/requirements.txt
RUN python -m venv /.venv \
    && /.venv/bin/pip install --upgrade pip \
    && /.venv/bin/pip install -r /app/requirements.txt

# Copy project
COPY . /app

ENV PATH="/.venv/bin:$PATH" \
    PYTHONPATH="/app"

EXPOSE 8000 5555

# Default command does nothing; each service sets its own command in docker-compose
CMD ["python", "-c", "print('Image ready')"]


