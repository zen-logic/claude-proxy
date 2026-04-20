"""Claude Code CLI proxy — use your own system prompt.

Replaces Anthropic's system prompt blocks with content you control
via a JSON config file. No logging, no conversation history. All
other traffic passes through untouched.

Usage:
    python proxy.py --rewrite [--port 9090] [--config config.json]

Then:
    HTTPS_PROXY=http://127.0.0.1:9090 NODE_EXTRA_CA_CERTS=./ca/ca.pem claude
"""

import argparse
import json
import os
import socket
import ssl
import select
import subprocess
import threading
import gzip
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

CA_DIR = Path(__file__).parent / "ca"
CA_KEY = CA_DIR / "ca.key"
CA_CERT = CA_DIR / "ca.pem"
CERT_DIR = Path(__file__).parent / "certs"

REWRITE_SYSTEM = False
CONFIG_PATH = None
PROMPT_LOGGED = False


def _load_config():
    """Load the system prompt config JSON file."""
    path = CONFIG_PATH or (Path(__file__).parent / "config.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return None


def _check_cert_valid(cert_path, key_path=None):
    """Check if a certificate exists and is not expired. Returns True if valid."""
    if not cert_path.exists():
        return False
    if key_path and not key_path.exists():
        return False

    result = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-checkend", "86400", "-noout"],
        capture_output=True,
    )
    return result.returncode == 0


def _create_ca():
    """Create a new CA key and certificate."""
    CA_DIR.mkdir(exist_ok=True)
    print("Creating new CA certificate...", flush=True)
    subprocess.run([
        "openssl", "req", "-x509", "-new", "-newkey", "rsa:2048",
        "-keyout", str(CA_KEY), "-out", str(CA_CERT),
        "-days", "825", "-nodes",
        "-subj", "/CN=Claude Proxy CA",
    ], capture_output=True, check=True)
    os.chmod(str(CA_KEY), 0o600)
    print(f"CA certificate created: {CA_CERT}", flush=True)


def _ensure_ca():
    """Ensure CA certificate exists and is valid, recreating if needed."""
    if _check_cert_valid(CA_CERT, CA_KEY):
        return

    print("CA certificate missing or expired, regenerating...", flush=True)

    # Remove old CA files
    CA_KEY.unlink(missing_ok=True)
    CA_CERT.unlink(missing_ok=True)
    (CA_DIR / "ca.srl").unlink(missing_ok=True)

    # Remove all host certs (they're signed by the old CA)
    if CERT_DIR.exists():
        for f in CERT_DIR.iterdir():
            f.unlink()

    _create_ca()


def _host_cert(hostname):
    """Get or create a cert for hostname signed by our CA."""
    CERT_DIR.mkdir(exist_ok=True)
    cert = CERT_DIR / f"{hostname}.pem"
    key = CERT_DIR / f"{hostname}.key"

    if _check_cert_valid(cert, key):
        return str(cert), str(key)

    # Remove stale files
    cert.unlink(missing_ok=True)
    key.unlink(missing_ok=True)

    # Key + CSR
    subprocess.run([
        "openssl", "req", "-newkey", "rsa:2048", "-keyout", str(key),
        "-out", str(CERT_DIR / f"{hostname}.csr"), "-nodes",
        "-subj", f"/CN={hostname}",
    ], capture_output=True, check=True)

    # SAN config
    san = CERT_DIR / f"{hostname}.cnf"
    san.write_text(
        f"[ext]\nsubjectAltName=DNS:{hostname}\n"
        f"basicConstraints=CA:FALSE\n"
        f"keyUsage=digitalSignature,keyEncipherment\n"
        f"extendedKeyUsage=serverAuth\n"
    )

    # Sign
    subprocess.run([
        "openssl", "x509", "-req",
        "-in", str(CERT_DIR / f"{hostname}.csr"),
        "-CA", str(CA_CERT), "-CAkey", str(CA_KEY), "-CAcreateserial",
        "-out", str(cert), "-days", "365",
        "-extfile", str(san), "-extensions", "ext",
    ], capture_output=True, check=True)

    # Clean up
    (CERT_DIR / f"{hostname}.csr").unlink(missing_ok=True)
    san.unlink(missing_ok=True)

    return str(cert), str(key)


def _log_original_prompt(system_blocks):
    """Log the original system prompt blocks on first run."""
    global PROMPT_LOGGED
    if PROMPT_LOGGED:
        return
    PROMPT_LOGGED = True

    prompt_dir = Path(__file__).parent / "prompt_log"
    prompt_dir.mkdir(exist_ok=True)

    for i, block in enumerate(system_blocks):
        text = block.get("text", "") if isinstance(block, dict) else str(block)
        (prompt_dir / f"block_{i}.txt").write_text(text)

    print(f"  ** ORIGINAL PROMPT LOGGED: {len(system_blocks)} blocks to {prompt_dir}/ **", flush=True)


def _rewrite_request(body: bytes, headers: dict) -> bytes:
    """Rewrite system prompt blocks per config.json."""
    config = _load_config()
    if not config:
        return body

    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body

    system = obj.get("system")
    if not isinstance(system, list):
        return body

    _log_original_prompt(system)

    replace_indices = set(config.get("replace_blocks", []))
    new_blocks = config.get("blocks", [])

    if not replace_indices or not new_blocks:
        return body

    rebuilt = []
    inserted = False
    for i, block in enumerate(system):
        if i in replace_indices:
            if not inserted:
                rebuilt.extend(new_blocks)
                inserted = True
        else:
            rebuilt.append(block)

    obj["system"] = rebuilt
    print(f"  ** SYSTEM PROMPT REWRITTEN (replaced blocks {sorted(replace_indices)}) **", flush=True)
    return json.dumps(obj).encode()


def _read_until_headers(sock):
    """Read from socket until we have complete HTTP headers."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None, None
        buf += chunk
    idx = buf.index(b"\r\n\r\n") + 4
    return buf[:idx], buf[idx:]


def _parse_headers(header_bytes):
    """Parse HTTP headers into (first_line, {lowercase_key: value})."""
    text = header_bytes.decode("utf-8", errors="replace")
    lines = text.rstrip("\r\n").split("\r\n")
    first = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return first, headers


def _read_body(sock, headers, initial):
    """Read the full HTTP body given headers and any bytes already read."""
    cl = headers.get("content-length")

    if cl:
        body = initial
        total = int(cl)
        while len(body) < total:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        return body
    else:
        return initial


def _read_chunked(sock, initial):
    """Read a chunked response properly by parsing chunk sizes."""
    buf = initial
    decoded = b""
    raw = initial

    while True:
        while b"\r\n" not in buf:
            more = sock.recv(65536)
            if not more:
                return raw, decoded
            buf += more
            raw += more

        line_end = buf.index(b"\r\n")
        size_str = buf[:line_end].decode("ascii", errors="replace").strip()

        if not size_str:
            buf = buf[line_end + 2:]
            continue

        try:
            chunk_size = int(size_str, 16)
        except ValueError:
            return raw, decoded

        if chunk_size == 0:
            buf = buf[line_end + 2:]
            while len(buf) < 2:
                more = sock.recv(65536)
                if not more:
                    break
                buf += more
                raw += more
            return raw, decoded

        buf = buf[line_end + 2:]
        while len(buf) < chunk_size + 2:
            more = sock.recv(65536)
            if not more:
                break
            buf += more
            raw += more

        decoded += buf[:chunk_size]
        buf = buf[chunk_size + 2:]

    return raw, decoded


def _decode_body(data, headers):
    if headers.get("content-encoding", "").lower() == "gzip":
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary {len(data)} bytes>"


class ProxyHandler(BaseHTTPRequestHandler):

    def do_CONNECT(self):
        host, port = self.path.split(":")
        port = int(port)

        if "anthropic.com" in host:
            self._mitm(host, port)
        else:
            self._tunnel(host, port)

    def _tunnel(self, host, port):
        """Blind passthrough for non-Anthropic traffic."""
        try:
            remote = socket.create_connection((host, port), timeout=10)
        except Exception as e:
            try:
                self.send_error(502, str(e))
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            return

        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
        except (BrokenPipeError, ConnectionError, OSError):
            remote.close()
            return

        conns = [self.connection, remote]

        while True:
            r, _, e = select.select(conns, [], conns, 120)
            if e or not r:
                break
            for s in r:
                other = remote if s is self.connection else self.connection
                try:
                    data = s.recv(65536)
                    if not data:
                        remote.close()
                        return
                    other.sendall(data)
                except Exception:
                    remote.close()
                    return
        remote.close()

    def _mitm(self, host, port):
        """Intercept Anthropic traffic."""
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
        except (BrokenPipeError, ConnectionError, OSError):
            return

        cert, key = _host_cert(host)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)

        try:
            client = ctx.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError) as e:
            print(f"Client SSL handshake failed: {e}")
            return

        try:
            while self._proxy_one(client, host, port):
                pass
        except (ConnectionError, ssl.SSLError, OSError):
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _proxy_one(self, client, host, port):
        """Handle one request/response pair on the MITM connection."""
        req_hdr_bytes, req_extra = _read_until_headers(client)
        if req_hdr_bytes is None:
            return False

        req_line, req_headers = _parse_headers(req_hdr_bytes)
        parts = req_line.split(" ", 2)
        if len(parts) < 2:
            return False
        method, path = parts[0], parts[1]

        req_body = _read_body(client, req_headers, req_extra or b"")

        # Rewrite system prompt for opus only (not haiku classifier)
        rewritten = False
        if REWRITE_SYSTEM and path.startswith("/v1/messages"):
            try:
                peek = json.loads(req_body)
                if "opus" in peek.get("model", ""):
                    req_body = _rewrite_request(req_body, req_headers)
                    rewritten = True
            except (json.JSONDecodeError, ValueError):
                pass

        # Connect to Anthropic — long timeout for opus thinking time
        remote = socket.create_connection((host, port), timeout=900)
        remote_ctx = ssl.create_default_context()
        remote = remote_ctx.wrap_socket(remote, server_hostname=host)
        remote.settimeout(900)

        # Forward request — use original headers unless body was rewritten
        if rewritten:
            fwd_hdr = f"{req_line}\r\n"
            for k, v in req_headers.items():
                if k == "content-length":
                    continue
                fwd_hdr += f"{k}: {v}\r\n"
            fwd_hdr += f"content-length: {len(req_body)}\r\n\r\n"
            remote.sendall(fwd_hdr.encode() + req_body)
        else:
            remote.sendall(req_hdr_bytes + req_body)

        # Read response
        resp_hdr_bytes, resp_extra = _read_until_headers(remote)
        if resp_hdr_bytes is None:
            remote.close()
            return False

        resp_line, resp_headers = _parse_headers(resp_hdr_bytes)
        te = resp_headers.get("transfer-encoding", "").lower()

        if te == "chunked":
            resp_body_raw, resp_body = _read_chunked(remote, resp_extra or b"")
            remote.close()
            client.sendall(resp_hdr_bytes + resp_body_raw)
        else:
            resp_body_raw = _read_body(remote, resp_headers, resp_extra or b"")
            remote.close()
            client.sendall(resp_hdr_bytes + resp_body_raw)

        # Minimal console output
        model = ""
        try:
            req_json = json.loads(req_body)
            if isinstance(req_json, dict):
                model = req_json.get("model", "")
        except (json.JSONDecodeError, ValueError):
            pass

        resp_status = resp_line.split(" ", 2)[1] if " " in resp_line else "?"
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        print(f"{ts}  {method} {path}  {resp_status}  {model}", flush=True)

        return path.startswith("/v1/messages")

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Claude Proxy")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--rewrite", action="store_true", help="Rewrite system prompt")
    parser.add_argument("--config", type=str, help="Path to config JSON file (default: config.json next to proxy.py)")
    args = parser.parse_args()

    global REWRITE_SYSTEM, CONFIG_PATH
    REWRITE_SYSTEM = args.rewrite
    if args.config:
        CONFIG_PATH = Path(args.config)

    _ensure_ca()

    # Redirect all output to log file
    import sys
    log_fh = open(Path(__file__).parent / "proxy.log", "a")
    sys.stdout = log_fh
    sys.stderr = log_fh

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ProxyHandler)
    server.daemon_threads = True
    print(f"Proxy listening on 127.0.0.1:{args.port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
