# PicoClaw

PicoClaw is a lightweight Telegram AI agent orchestrator for Raspberry Pi 4 (1GB RAM, aarch64).

It runs as two processes:
- `picoclaw.service` for Telegram interactions
- `picoclaw-worker.service` for asynchronous jobs

## Features

- Multi-agent routing (`researcher`, `coder`, `ops`)
- Synchronous (`/ask`) and async queued jobs (`/task`)
- SQLite-backed skills with per-chat enable/disable
- SQLite-backed persistent chat memory (`MEMORY ...`)
- SQLite persistence with WAL mode
- User allowlist security (`ALLOWED_USER_IDS`)
- OpenAI-compatible LLM support (`openai` or `openrouter`)

## Repository Layout

- `app/main.py` Telegram bot handlers
- `app/worker.py` queue worker loop
- `app/router.py` agent routing
- `app/agents/` pure agent logic
- `app/db.py` SQLite helpers
- `app/llm.py` HTTP LLM client
- `scripts/init_db.py` database bootstrap
- `systemd/` unit files

## Install on Raspberry Pi from GitHub

Run on the Pi (Raspberry Pi OS, Python 3.11+):

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo mkdir -p /opt/picoclaw
sudo chown -R pi:pi /opt/picoclaw
git clone https://github.com/rozdol/picoclaw.git /opt/picoclaw
cd /opt/picoclaw
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill required variables.

Initialize SQLite:

```bash
source /opt/picoclaw/venv/bin/activate
cd /opt/picoclaw
python3 -m scripts.init_db
```

Install and start services:

```bash
cd /opt/picoclaw
sudo cp systemd/picoclaw.service /etc/systemd/system/
sudo cp systemd/picoclaw-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable picoclaw.service picoclaw-worker.service
sudo systemctl start picoclaw.service picoclaw-worker.service
```

Check status and logs:

```bash
sudo systemctl status picoclaw.service picoclaw-worker.service
sudo journalctl -u picoclaw.service -f
sudo journalctl -u picoclaw-worker.service -f
```

## Setup (Existing Checkout)

1. Create and activate virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create config file:

```bash
cp .env.example .env
```

4. Edit `.env` and fill required variables.

## Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Run `/newbot` and follow prompts.
3. Copy the bot token into `TELEGRAM_BOT_TOKEN`.

## Get Your Numeric Telegram User ID

Options:
- Message [@userinfobot](https://t.me/userinfobot) and copy your `Id`.
- Or run PicoClaw and use `/whoami`.

Then set `ALLOWED_USER_IDS` (comma-separated) in `.env`.

Important: if `ALLOWED_USER_IDS` is empty, all users are denied.

## Configuration

Required:
- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_USER_IDS`
- `LLM_PROVIDER`
- Provider API key (`OPENAI_API_KEY` or `OPENROUTER_API_KEY`)

Optional key settings:
- `DB_PATH` (default `./picoclaw.db`)
- `REQUIRE_APPROVAL_FOR_OPS` (`1` or `0`)
- `WORKER_POLL_INTERVAL_SECONDS`
- `LOG_LEVEL`

## Initialize Database

```bash
source venv/bin/activate
python3 -m scripts.init_db
```

This creates SQLite tables and enables WAL mode.

## Run Locally

Terminal 1:

```bash
source venv/bin/activate
python3 -m app.main
```

Terminal 2:

```bash
source venv/bin/activate
python3 -m app.worker
```

## Telegram Commands

- `/start` show help
- `/whoami` show user/chat ID and auth state
- `/agents` list agents
- `/use <agent>` set default chat agent
- `/ask <text>` synchronous run
- `/task <text>` enqueue async job
- `/jobs` list recent jobs
- `/approve <job_id>` approve an ops job waiting for approval
- `/skills` list skills and chat state
- `/skill_add <name> | <instructions>` create or update skill and enable it for chat
- `/skill_enable <name>` enable skill for chat
- `/skill_disable <name>` disable skill for chat
- `MEMORY <text>` save memory for this chat
- `MEMORY LIST` list saved memories
- `MEMORY DELETE <id>` delete one memory item
- `MEMORY CLEAR` clear all memories for this chat

## Skills

Skills are reusable instruction snippets stored in SQLite.

- They are global definitions (`/skill_add`), but enabled per chat.
- Active chat skills are injected as additional system instructions for both `/ask` and `/task`.
- Async jobs snapshot active skills at queue time to keep execution deterministic.

## Memory

Persistent memory stores short chat-specific facts/preferences in SQLite.

- `MEMORY ...` commands are handled directly by the bot (no LLM call).
- Saved memory is injected as additional system context for both synchronous and async runs.
- Async jobs snapshot memory at queue time to keep behavior deterministic.

## Install systemd Services

Assuming deployment path `/opt/picoclaw`:

```bash
sudo cp systemd/picoclaw.service /etc/systemd/system/
sudo cp systemd/picoclaw-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable picoclaw.service picoclaw-worker.service
sudo systemctl start picoclaw.service picoclaw-worker.service
```

## Tail Logs

```bash
sudo journalctl -u picoclaw.service -f
sudo journalctl -u picoclaw-worker.service -f
```

## Troubleshooting

- Bot exits on startup:
  - Confirm `TELEGRAM_BOT_TOKEN` is set and valid.
- Commands return `Access denied`:
  - Verify your numeric user ID is included in `ALLOWED_USER_IDS`.
- Worker does not process jobs:
  - Confirm worker service is running and LLM API credentials are valid.
- Jobs stuck in `needs_approval`:
  - Use `/approve <job_id>` from an authorized user.
- SQLite locked errors:
  - Keep both processes on the same DB path and avoid manual writes.

## Verification Checklist

- `python3 -m app.main` starts without crash
- `python3 -m app.worker` starts without crash
- DB file exists at `DB_PATH`
- WAL mode active (`python3 -m scripts.init_db` output)
- Telegram commands respond
- Job lifecycle transitions through expected statuses
- Systemd units start and restart cleanly
- Memory usage remains stable on Raspberry Pi 4
