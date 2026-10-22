# Magpie: voice-first notes, filed by the AI of your choice.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 1000 magpie \
    && mkdir /data \
    && chown magpie:magpie /data

USER magpie
# The commit this image was built from: open dashboards reload after an update.
ARG MAGPIE_BUILD=""
ENV MAGPIE_BUILD=$MAGPIE_BUILD \
    MAGPIE_DATA_DIR=/data \
    MAGPIE_HOST=0.0.0.0 \
    MAGPIE_PORT=8000
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["magpie", "serve"]
