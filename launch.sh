#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PROXY_PID=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [config-path] [claude-args...]

Launches the Claude proxy on a free port, then starts claude routed
through it with the proxy's CA cert trusted.

Arguments:
  config-path   Optional path to a config JSON file.
                Defaults to config.json next to proxy.py.
                Treated as the config path only if it does not
                start with '--'; otherwise passed through to claude.

  claude-args   Any additional arguments (typically starting with '--')
                are passed through to the claude CLI.

Examples:
  $(basename "$0")
      Default config, plain claude.

  $(basename "$0") --model claude-opus-4-6[1M]
      Default config, claude launched with the given model.

  $(basename "$0") /path/to/custom_config.json
      Custom config, plain claude.

  $(basename "$0") /path/to/custom_config.json --model claude-opus-4-6[1M]
      Custom config and a specific model.

Options:
  -h, --help    Show this help and exit.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

# Optional first positional argument: path to config JSON.
# Any remaining arguments are passed through to the claude CLI.
CONFIG_ARG=""
if [[ -n "${1:-}" && "$1" != --* ]]; then
    CONFIG_ARG="--config $1"
    shift
fi

# Find a free port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

# Start the proxy
python3 "$DIR/proxy.py" --rewrite --port "$PORT" $CONFIG_ARG &
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
claude "$@"
