# Home Assistant add-on: Taipower AMI API server.
#
# Multi-arch (amd64 + aarch64). Camoufox publishes both x86_64 and arm64 Linux
# builds and solves Turnstile in the same browser that submits the login, so
# no separate solver service is involved.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# /data is the add-on's persistent storage.
ENV TAIPOWER_OUT_DIR=/data/output

# Firefox/Camoufox runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgtk-3-0 \
        libdbus-glib-1-2 \
        libxt6 \
        libx11-xcb1 \
        libasound2 \
        libpci3 \
        libegl1 \
        libgl1 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    python-dotenv \
    "camoufox[geoip]" \
    fastapi \
    uvicorn \
    requests

# Pre-fetch the arch-appropriate Camoufox browser binaries.
RUN camoufox fetch

# `src/` must be synced into this directory first — run ./sync_src.sh.
WORKDIR /app
COPY src /app/src
COPY addon-entrypoint.sh /usr/bin/addon-entrypoint.sh
RUN chmod +x /usr/bin/addon-entrypoint.sh
ENV PYTHONPATH=/app/src

EXPOSE 8000

ENTRYPOINT ["/usr/bin/addon-entrypoint.sh"]
