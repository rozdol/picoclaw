# `AGENTS.md`

## Project: PicoClaw

Lightweight Telegram AI agent orchestrator designed for Raspberry Pi 4 (1GB RAM, aarch64).

---

## Core Philosophy

PicoClaw must be:

- Memory efficient
- Stable 24/7
- Minimal dependencies
- Easy to debug
- Deterministic
- Systemd-native
- SQLite-based
- Python-only

No trendy stacks. No heavy frameworks. No containers.

---

## Hard Constraints (Non-Negotiable)

### Runtime
- Python 3.11+
- No Node.js
- No Docker
- No Kubernetes
- No Redis unless explicitly requested
- No background web UI frameworks

### Memory Discipline
- Must run on 1GB RAM device
- Target steady-state memory: < 300MB
- No in-memory queues for jobs (persist everything)
- All async tasks must be short-lived or queued

### Persistence
- SQLite only
- WAL mode enabled
- No ORM
- Direct SQL with minimal abstraction

### Services
- Must run under systemd
- Separate services:
  - `picoclaw.service` (telegram bot)
  - `picoclaw-worker.service` (job processor)
- Restart=always
- No process managers like PM2

### LLM
- API-based only (OpenAI-compatible)
- No local inference
- No embedding stores
- No vector DB

---

## Architecture Rules

### 1. Separation of Concerns

- `main.py` → Telegram interface only
- `worker.py` → async job executor only
- `router.py` → agent selection logic only
- `agents/*` → pure agent logic, no Telegram or DB
- `db.py` → SQLite helpers only
- `llm.py` → HTTP LLM client only

No cross-layer shortcuts.

---

### 2. Agents Must Be Pure

Agents:
- Receive string
- Return string
- No Telegram objects
- No DB access
- No side effects

---

### 3. Security Model

- Default deny
- Explicit allowlist from `.env`
- If `ALLOWED_USER_IDS` empty → deny all
- No hidden bypasses

---

### 4. Job System

- Status lifecycle:
  - queued
  - running
  - done
  - error
  - needs_approval
  - cancelled
- Worker picks oldest queued job
- Must be idempotent
- No busy loops

---

### 5. Error Handling

- Never crash on bad LLM response
- Catch HTTP errors
- Truncate long Telegram messages
- Log errors clearly

---

### 6. Logging

- Use Python logging module
- No print() in production
- Logs visible via:
  - `journalctl -u picoclaw.service`
  - `journalctl -u picoclaw-worker.service`

---

### 7. Configuration

All configuration must come from:

- `.env`
- Environment variables

No hardcoded secrets.

---

## Performance Principles

- Prefer simple functions over abstractions
- Avoid dependency bloat
- Avoid reflection / dynamic imports
- Avoid plugin systems unless explicitly requested
- Keep startup time under 2 seconds

---

## Extension Rules

When adding new features:

1. Do not increase memory footprint significantly.
2. Do not introduce background threads unless necessary.
3. Avoid external services.
4. Keep DB schema simple.
5. Keep migrations manual and explicit.

---

## Anti-Patterns (Forbidden)

- Dockerfiles
- Redis queues
- ORM frameworks
- Background schedulers unless minimal
- Heavy AI frameworks
- Web dashboards
- WebSocket gateways
- Multi-process pools

---

## Expected Deployment

Raspberry Pi 4  
1GB RAM  
aarch64  
Tailscale enabled  
Runs 24/7  

---

## Verification Checklist

Before considering a feature complete:

- `python3 -m scripts.init_db` runs without crash
- `python3 -m app.main` runs without crash
- `python3 -m app.worker` runs without crash
- DB file created
- WAL mode active
- Telegram commands respond
- Job lifecycle works
- Systemd units start cleanly
- No memory spikes > 400MB during normal operation

---

## Future Direction

PicoClaw is:

- A control plane
- A Telegram-first AI orchestrator
- Not a web app
- Not an LLM host
- Not a framework

Keep it sharp. Keep it small.
