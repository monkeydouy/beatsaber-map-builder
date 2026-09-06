# ---------------------------------------------------------------------------
# SaberMapper AI — mapper API
#
# ffmpeg is a hard requirement (every input is transcoded through it) and
# yt-dlp is installed from PyPI so it can be updated independently of the
# distro, which matters because YouTube changes frequently.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NUMBA_CACHE_DIR=/tmp/numba_cache

# libsndfile1 backs soundfile; ffmpeg covers both decode and encode.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user; storage is the only writable path it needs.
RUN useradd --create-home --uid 10001 mapper \
    && mkdir -p /storage \
    && chown -R mapper:mapper /storage /app
USER mapper

ENV STORAGE_PATH=/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
