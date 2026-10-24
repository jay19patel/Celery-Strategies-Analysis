FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TZ=UTC

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
COPY pyproject.toml /app/

RUN uv venv /opt/venv && uv pip install .

COPY app /app/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
