# CodeX Prompt — Build PicoClaw (Telegram AI Agent Orchestrator for Raspberry Pi)

Build a production-ready, lightweight Telegram “agent orchestrator” called **PicoClaw**, optimized for Raspberry Pi 4 (1GB RAM, aarch64). Use Python only. Do NOT use Node.js, Docker, Redis, or heavy frameworks.

---

## Objectives

Create a minimal, stable Telegram-based AI agent orchestrator that:

- Routes messages to multiple agents (researcher, coder, ops)
- Uses LLM via API (OpenAI-compatible + OpenRouter support)
- Supports synchronous and asynchronous task execution
- Persists state in SQLite (WAL mode)
- Runs as two systemd services
- Uses environment variables only for configuration
- Is optimized for low RAM usage

---

## Required Telegram Commands

- `/start` → show help
- `/whoami` → show user_id + chat_id + authorization status
- `/agents` → list available agents
- `/use <agent>` → set default agent for chat
- `/ask <text>` → run synchronously
- `/task <text>` → enqueue async job
- `/jobs` → list recent jobs
- `/approve <job_id>` → approve ops job (if required)

---

## Technical Constraints

### Runtime
- Python 3.11+
- python-telegram-bot (async)
- httpx
- python-dotenv
- SQLite (WAL mode)
- No ORM
- No background frameworks

### Memory Discipline
- Must run on 1GB RAM device
- Target steady-state memory < 300MB
- No in-memory queues
- All jobs persisted in SQLite

### Services
Two systemd units:
- `picoclaw.service` → Telegram bot
- `picoclaw-worker.service` → job worker
Restart=always

---

## Architecture Requirements

### Structure

Create this repository layout:

picoclaw/
- app/
  - main.py
  - config.py
  - security.py
  - db.py
  - llm.py
  - router.py
  - worker.py
  - agents/
    - researcher.py
    - coder.py
    - ops.py
- scripts/
  - init_db.py
- systemd/
  - picoclaw.service
  - picoclaw-worker.service
- requirements.txt
- .env.example
- README.md

---

## Agent Rules

Agents must:
- Receive a string
- Return a string
- Have no DB or Telegram dependencies
- Contain only LLM logic

---

## LLM Implementation

Implement:

async def chat_completion(system: str, user: str) -> str

Support:

LLM_PROVIDER=openai  
- Use OPENAI_API_KEY  
- Endpoint: https://api.openai.com/v1/chat/completions  
- Default model: gpt-4o-mini  

LLM_PROVIDER=openrouter  
- Use OPENROUTER_API_KEY  
- Endpoint: https://openrouter.ai/api/v1/chat/completions  
- Model from OPENROUTER_MODEL  

Handle:
- HTTP errors
- Non-200 responses
- Timeouts
- Unexpected JSON structures

---

## Job Lifecycle

Statuses:
- queued
- running
- done
- error
- needs_approval
- cancelled

Rules:
- Worker picks oldest queued job
- If REQUIRE_APPROVAL_FOR_OPS=1 and agent == "ops"
  → set status to needs_approval
- `/approve` moves job back to queued
- Results stored in DB

---

## Security

- Allowlist Telegram user IDs via ALLOWED_USER_IDS env var
- If empty → deny all
- No bypasses

---

## Logging

- Use Python logging module
- No print() in production
- Logs must be visible via:
  - journalctl -u picoclaw.service
  - journalctl -u picoclaw-worker.service

---

## README Must Include

- Setup steps (venv + install)
- Telegram bot creation instructions
- How to get Telegram numeric user ID
- How to configure .env
- How to run locally
- How to install systemd services
- How to tail logs
- Troubleshooting section

---

## Verification Checklist

Ensure:

- python -m app.main runs
- python -m app.worker runs
- SQLite DB created
- WAL mode enabled
- Telegram commands work
- Job lifecycle works
- Systemd services start cleanly
- Memory usage reasonable (<400MB peak)

---

Proceed to implement the full repository now.
Do not ask clarification questions.
Make reasonable production-grade decisions where unspecified.