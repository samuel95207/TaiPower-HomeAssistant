#!/bin/bash
set -e

# Ensure output directory exists.
mkdir -p /app/data

# If no explicit command was given, run the default Camoufox dashboard scraper.
if [ $# -eq 0 ]; then
    exec xvfb-run --server-args="-screen 0 1440x900x24" -a \
        python3 src/run_scraper.py \
        --browser-type camoufox \
        --no-persistent \
        --no-interactive-fallback
else
    exec "$@"
fi
