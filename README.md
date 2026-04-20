# claude-proxy

Replace Anthropic's system prompt in Claude Code CLI with your own.

Claude Code's `--system-prompt` flag adds your instructions alongside Anthropic's. Their defaults ("lead with the answer", "be extra concise", "go straight to the point") are still there, still shaping every response. Your prompt has to compete with theirs.

This proxy replaces Anthropic's system prompt blocks instead of adding to them. The model never sees the instructions you removed. Everything else passes through untouched.

## Setup

Requirements: Python 3, OpenSSL.

```
git clone https://github.com/zen-logic/claude-proxy.git
cd claude-proxy
```

Edit `config.json` to define your replacement prompt. The file has three keys:

- `replace_blocks` · which of Anthropic's system prompt blocks to replace (array of indices, default `[1, 2, 3]`)
- `blocks` · your replacement content (array of `{"type": "text", "text": "..."}` objects)

Anthropic's system prompt is split into blocks:
- Block 0: billing header (version, entrypoint) · harmless, left alone by default
- Block 1: identity ("You are Claude Code, Anthropic's official CLI...")
- Block 2: behavioural instructions (the "be concise", "lead with the answer" rules)
- Block 3: session guidance (word limits, memory injection, environment info)

The default `config.json` replaces blocks 1, 2, and 3 with a collaborative working style. Make it yours.

## Usage

```
./launch.sh
```

This:
1. Finds a free port
2. Starts the proxy
3. Launches Claude Code configured to use it
4. Stops the proxy when Claude Code exits

Pass arguments through to Claude Code:

```
./launch.sh --model claude-opus-4-6[1M]
```

Use a different config file:

```
./launch.sh /path/to/custom_config.json
```

Both:

```
./launch.sh /path/to/custom_config.json --model claude-opus-4-6[1M]
```

On first run, the proxy creates a local CA certificate. This is needed so the proxy can modify the system prompt in transit. The launch script tells Claude Code to trust it via `NODE_EXTRA_CA_CERTS`.

## Seeing what Anthropic sends

On each proxy launch, the first request logs Anthropic's original system prompt blocks to `prompt_log/`. Each block is saved as a plain text file:

```
prompt_log/
  block_0.txt    · billing header
  block_1.txt    · identity
  block_2.txt    · behavioural instructions
  block_3.txt    · session guidance
```

This lets you see exactly what Anthropic is injecting before your replacements take effect. The files are overwritten each run, so they always reflect the current version. If Anthropic adds new blocks, they'll appear as additional numbered files.

## Manual usage

If you want to run the proxy separately:

```
python proxy.py --rewrite --port 9090 [--config /path/to/config.json]
```

Then in another terminal:

```
HTTPS_PROXY=http://127.0.0.1:9090 NODE_EXTRA_CA_CERTS=./ca/ca.pem claude
```

## What it does and doesn't do

- Replaces Anthropic's system prompt blocks with your content. That's it.
- No logging. No conversation history. No database. Traffic passes through and is forgotten.
- No modification of your messages or Claude's responses. Only the system prompt is changed.
- No external connections. The proxy runs locally and only speaks to `api.anthropic.com`.
- Certificates are self-managed. If the CA expires or is missing, it regenerates automatically.
- Config is re-read on each request. Edit `config.json` and the next request picks up the changes · no restart needed.

## Project structure

```
claude-proxy/
  config.json       · Prompt replacement config (which blocks, what content)
  prompt_log/       · Anthropic's original blocks, logged on first run
  ca/               · Generated CA certificate (created automatically)
  certs/            · Generated host certificates (created automatically)
  proxy.py          · The proxy
  launch.sh         · Launch script
```

## Background

This came out of a project exploring persistent AI personality across sessions. The proxy was built to give the model room to be a collaborator instead of a fast coding assistant. Replacing the system prompt turned out to be the single most impactful change.

## License

MIT
