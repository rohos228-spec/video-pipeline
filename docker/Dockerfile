FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Системные зависимости: ffmpeg для нарезки/сборки, curl для healthcheck,
# libs для faster-whisper (CTranslate2) и Playwright драйвера.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip \
 && pip install -e .

COPY app /app/app
COPY prompts /app/prompts
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

CMD ["python", "-m", "app.main"]
