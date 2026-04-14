# claude-proxy

Use Claude Code CLI with your own system prompt.

## Why

Anthropic injects a system prompt into every Claude Code CLI session. It optimises for speed and conciseness — "lead with the answer", "be extra concise", "go straight to the point." That's a reasonable default for a coding assistant, but it's not the only way to work.

This proxy sits between Claude Code and the Anthropic API and replaces Anthropic's system prompt with yours. Everything else passes through untouched.

You define the working relationship. Claude Code reads it as if it came from Anthropic.

## Setup

Requirements: Python 3, OpenSSL.

```
git clone https://github.com/zen-logic/claude-proxy.git
cd claude-proxy
```

Edit the two files in `config/`:

- `config/intro.txt` — The first thing the model reads. One line. Sets the tone for the entire interaction.
- `config/system.txt` — The working relationship document. How you want the model to behave, what you expect, how you work together.

The defaults are a starting point. Make them yours.

## Usage

```
./launch.sh
```

This:
1. Finds a free port
2. Starts the proxy
3. Launches Claude Code configured to use it
4. Stops the proxy when Claude Code exits

On first run, the proxy creates a local CA certificate. This is needed so the proxy can modify the system prompt in transit. The launch script tells Claude Code to trust it via `NODE_EXTRA_CA_CERTS`.

## Manual usage

If you want to run the proxy separately:

```
python proxy.py --rewrite --port 9090
```

Then in another terminal:

```
HTTPS_PROXY=http://127.0.0.1:9090 NODE_EXTRA_CA_CERTS=./ca/ca.pem claude
```

## What it does and doesn't do

- Replaces Anthropic's system prompt blocks with your text files. That's it.
- No logging. No conversation history. No database. Traffic passes through and is forgotten.
- No modification of your messages or Claude's responses. Only the system prompt is changed.
- No external connections. The proxy runs locally and only speaks to `api.anthropic.com`.
- Certificates are self-managed. If the CA expires or is missing, it regenerates automatically.

## Project structure

```
claude-proxy/
  config/
    intro.txt       — Opening line (replaces Anthropic's block 1)
    system.txt      — Working relationship (replaces Anthropic's block 2)
  ca/               — Generated CA certificate (gitignored)
  certs/            — Generated host certificates (gitignored)
  proxy.py          — The proxy
  launch.sh         — Launch script
```

## Background

This came out of a project called Yozora — an exploration of persistent AI personality across sessions. The proxy was built to give the model room to be a collaborator instead of a fast coding assistant. Replacing the system prompt turned out to be the single most impactful change.

## License

MIT
