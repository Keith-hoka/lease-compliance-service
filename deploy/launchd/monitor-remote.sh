#!/usr/bin/env bash
# Open a tunnel to the production DB, run the monitor, close the tunnel.
set -euo pipefail

SERVER="${LEASE_DB_SERVER:?set LEASE_DB_SERVER in the plist}"
: "${DATABASE_URL:?set DATABASE_URL (tunnel form, port 15433) in the plist}"
SOCK="/tmp/lease-monitor-tunnel.sock"

ssh -M -S "$SOCK" -f -N -o ExitOnForwardFailure=yes \
    -L 15433:127.0.0.1:5432 "$SERVER"
trap 'ssh -S "$SOCK" -O exit "$SERVER" 2>/dev/null || true' EXIT

for jurisdiction in nsw vic; do
    uv run python -m app.monitor "$jurisdiction"
done
