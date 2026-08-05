#!/bin/bash
set -e

# Read add-on options (written by the HA Supervisor) into the TAIPOWER_* env
# vars that taipower_ami.auth.load_credentials understands.
if [ -f /data/options.json ]; then
    eval "$(python3 - <<'PY'
import json
import shlex

opts = json.load(open("/data/options.json"))
mapping = {
    "username": "TAIPOWER_USER",
    "password": "TAIPOWER_PASSWORD",
}
for key, var in mapping.items():
    value = opts.get(key) or ""
    print(f"export {var}={shlex.quote(str(value))}")
PY
)"
fi

mkdir -p "${TAIPOWER_OUT_DIR:-/data/output}"

exec uvicorn taipower_ami.api:app --host 0.0.0.0 --port 8000
