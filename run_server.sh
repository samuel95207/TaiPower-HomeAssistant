#!/bin/bash
set -e

# run_server.sh — build the Taipower AMI Docker image and start the HTTP API server.
#
# Usage:
#   ./run_server.sh             # build and run API on http://localhost:8000
#   ./run_server.sh --detach    # run in background
#   ./run_server.sh --build     # force rebuild before running
#   ./run_server.sh --stop      # stop the running container

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="taipower-ami"
IMAGE_NAME="taipower-ami:latest"
CONTAINER_NAME="taipower_ami_api"
PORT="8000"

DETACH=false
FORCE_BUILD=false
STOP=false

for arg in "$@"; do
    case "$arg" in
        --detach|-d) DETACH=true ;;
        --build|-b) FORCE_BUILD=true ;;
        --stop) STOP=true ;;
        --help|-h)
            echo "Usage: $0 [--detach|-d] [--build|-b] [--stop] [--help|-h]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--detach|-d] [--build|-b] [--stop] [--help|-h]"
            exit 1
            ;;
    esac
done

if [ "$STOP" = true ]; then
    echo "Stopping ${CONTAINER_NAME}..."
    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    echo "Stopped."
    exit 0
fi

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo "ERROR: .env not found at ${SCRIPT_DIR}/.env"
    echo "Please create it with USER and PASSWORD."
    exit 1
fi

# Build if image doesn't exist or if --build was passed.
if [ "$FORCE_BUILD" = true ] || ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "Building ${IMAGE_NAME}..."
    docker build -f "${SCRIPT_DIR}/docker/Dockerfile" -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
else
    echo "Using existing image ${IMAGE_NAME}. Pass --build to rebuild."
fi

# Stop any existing container with the same name.
docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "Starting Taipower AMI API server on http://localhost:${PORT}..."

RUN_ARGS=(
    --rm
    --name "${CONTAINER_NAME}"
    -p "${PORT}:8000"
    -v "${SCRIPT_DIR}/.env:/app/.env:ro"
    -v "${SCRIPT_DIR}/data:/app/data"
)

if [ "$DETACH" = true ]; then
    RUN_ARGS+=(-d)
fi

RUN_ARGS+=(
    "${IMAGE_NAME}"
    uvicorn taipower_ami.api:app --host 0.0.0.0 --port 8000
)

docker run "${RUN_ARGS[@]}"

if [ "$DETACH" = true ]; then
    echo "Container is running in background: ${CONTAINER_NAME}"
    echo "Logs: docker logs -f ${CONTAINER_NAME}"
    echo "Stop:  docker stop ${CONTAINER_NAME}"
fi
