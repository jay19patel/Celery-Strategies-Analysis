# Base image - lightweight Python runtime
FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Copy requirements first for Docker layer caching
# If requirements don't change, this layer is reused
COPY requirements.txt .

# Install Python dependencies
# --no-cache-dir: Don't cache packages (smaller image size)
# --upgrade pip: Use latest pip version for faster installs
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
# Done after pip install for better Docker layer caching
COPY . .

# Environment variables for Python and Celery
ENV PYTHONPATH=/app
# PYTHONUNBUFFERED: Show logs immediately (important for monitoring Celery tasks)
ENV PYTHONUNBUFFERED=1

# Default command - run Celery worker
# --loglevel=info: Show task execution logs
# --concurrency=20: Run 20 parallel workers (optimal for 100 tasks)
CMD ["celery", "-A", "core.celery_config", "worker", "--loglevel=info", "--concurrency=20"]