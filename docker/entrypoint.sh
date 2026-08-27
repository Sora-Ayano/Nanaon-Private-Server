#!/bin/sh
set -eu

# Application root is the parent of docker/ (repo root locally, /app/app in the image).
cd "$(dirname "$0")/.."

# Reset if database is empty
if [ ! -s data/private_server.sqlite3 ] && [ -s seed.sqlite3 ]; then
    echo "[entrypoint] empty database found; seeding from seed.sqlite3"
    cp seed.sqlite3 data/private_server.sqlite3
fi

if [ ! -f var/certs/gateway_trust_cert.pem ] || [ ! -f var/certs/game_key.pem ]; then
    echo "[entrypoint] no gateway certificate found; generating it in var/certs/"
    python generate_gateway_certificate.py
fi

python run.py "$@" &
launcher=$!

trap 'kill -INT "$launcher" 2>/dev/null' TERM INT

wait "$launcher" && exit 0 || exit "$?"
