#!/bin/bash
# Copy the taipower_ami package into the add-on folder so the Supervisor can
# build it with the add-on directory as the Docker build context.
set -e
cd "$(dirname "$0")"
rm -rf src
mkdir -p src
cp -R ../../src/taipower_ami src/taipower_ami
find src -name __pycache__ -type d -exec rm -rf {} +
echo "Synced src/taipower_ami into $(pwd)/src"
