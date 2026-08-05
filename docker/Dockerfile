# Dockerfile for the Taipower AMI scraper (amd64 + aarch64).
#
# Camoufox solves Turnstile in the same browser that submits the login, so no
# separate solver service is needed; that also frees the image from the
# amd64-only Chrome dependency the Turnstile-Solver image carried.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TAIPOWER_OUT_DIR=/app/data

# Firefox/Camoufox runtime libraries; xvfb backs the CLI scraper's visible
# fallback browser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        xvfb \
        xauth \
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

# Pre-fetch Camoufox browser binaries so the first scrape doesn't have to.
RUN camoufox fetch

# Copy and install the taipower_ami package.
WORKDIR /app
COPY src/taipower_ami ./src/taipower_ami
COPY src/run_scraper.py ./src/run_scraper.py
COPY src/run_fetcher.py ./src/run_fetcher.py
COPY docker/entrypoint.sh /usr/bin/run-taipower.sh
RUN chmod +x /usr/bin/run-taipower.sh
ENV PYTHONPATH=/app/src

# Expose the HTTP API port.
EXPOSE 8000

# Override the base image entrypoint so our scraper/API actually runs.
ENTRYPOINT ["/usr/bin/run-taipower.sh"]

# Default: run the main dashboard scraper with Camoufox.
# Override CMD to run the API server, e.g.:
#   docker run -p 8000:8000 ... uvicorn taipower_ami.api:app --host 0.0.0.0 --port 8000
CMD ["xvfb-run", "--server-args=-screen 0 1440x900x24", "-a", \
     "python3", "src/run_scraper.py", \
     "--browser-type", "camoufox", \
     "--no-persistent", \
     "--no-interactive-fallback"]
