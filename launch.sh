#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PROXY_PID=""

# Find a free port
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")

# Start the proxy
python3 "$DIR/proxy.py" --rewrite --port "$PORT" &
PROXY_PID=$!
sleep 1

# Check it started
if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "Proxy failed to start"
    exit 1
fi
echo "Proxy running on port $PORT (pid $PROXY_PID)"

# Stop proxy on exit
cleanup() {
    if [ -n "$PROXY_PID" ] && kill -0 "$PROXY_PID" 2>/dev/null; then
        kill "$PROXY_PID" 2>/dev/null
        wait "$PROXY_PID" 2>/dev/null
        echo "Proxy stopped"
    fi
}
trap cleanup EXIT

HTTPS_PROXY=http://127.0.0.1:$PORT \
NODE_EXTRA_CA_CERTS="$DIR/ca/ca.pem" \
claude
