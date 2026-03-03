from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from pathlib import Path
from typing import TextIO

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import SETTINGS, configure_logging
from app.db import approve_job, create_job, get_default_agent, init_db, list_recent_jobs, set_default_agent
from app.router import available_agents, is_valid_agent, run_agent
from app.security import is_user_allowed

logger = logging.getLogger(__name__)
_BOT_LOCK_HANDLE: TextIO | None = None
_BOT_LOCK_PATH = Path("/tmp/picoclaw-bot.lock")


def _acquire_single_instance_lock() -> None:
    global _BOT_LOCK_HANDLE

    lock_handle = _BOT_LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_handle.close()
        raise RuntimeError(
            "Another PicoClaw bot instance is already running on this host."
        ) from exc

    lock_handle.write(f"{os.getpid()}\n")
    lock_handle.flush()
    _BOT_LOCK_HANDLE = lock_handle
    logger.info("Single-instance lock acquired: path=%s pid=%s", _BOT_LOCK_PATH, os.getpid())


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    return is_user_allowed(user.id if user else None)


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("Access denied.")


async def _reply_safe(update: Update, text: str) -> None:
    if not update.message:
        return
    if len(text) > SETTINGS.max_telegram_message_length:
        text = text[: SETTINGS.max_telegram_message_length - 24] + "\n\n[truncated by PicoClaw]"
    await update.message.reply_text(text)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return

    help_text = (
        "PicoClaw commands:\n"
        "/start - show this help\n"
        "/whoami - show Telegram IDs and auth state\n"
        "/agents - list available agents\n"
        "/use <agent> - set default agent for this chat\n"
        "/ask <text> - run synchronous agent request\n"
        "or send plain text to chat naturally with the selected agent\n"
        "/task <text> - enqueue async job\n"
        "/jobs - list recent jobs\n"
        "/approve <job_id> - approve waiting ops job"
    )
    await _reply_safe(update, help_text)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    user_id = user.id if user else None
    chat_id = chat.id if chat else None
    authorized = is_user_allowed(user_id)

    await _reply_safe(
        update,
        f"user_id: {user_id}\nchat_id: {chat_id}\nauthorized: {'yes' if authorized else 'no'}",
    )


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return

    agent_list = "\n".join(f"- {name}" for name in available_agents())
    await _reply_safe(update, f"Available agents:\n{agent_list}")


async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat or not update.message:
        return

    if not context.args:
        await _reply_safe(update, "Usage: /use <agent>")
        return

    agent_name = context.args[0].strip().lower()
    if not is_valid_agent(agent_name):
        await _reply_safe(update, "Unknown agent. Use /agents to list options.")
        return

    set_default_agent(update.effective_chat.id, agent_name)
    await _reply_safe(update, f"Default agent set to: {agent_name}")


async def _run_sync_prompt(update: Update, prompt: str) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return

    if not prompt:
        return

    agent_name = get_default_agent(update.effective_chat.id)
    await _reply_safe(update, f"Running `{agent_name}` synchronously...")

    try:
        result = await run_agent(agent_name, prompt)
    except Exception as exc:
        logger.exception("Synchronous ask failed")
        await _reply_safe(update, f"Request failed: {exc}")
        return

    await _reply_safe(update, result)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(context.args).strip()
    if not prompt:
        await _reply_safe(update, "Usage: /ask <text>")
        return
    await _run_sync_prompt(update, prompt)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    prompt = (update.message.text or "").strip()
    if not prompt:
        return

    await _run_sync_prompt(update, prompt)


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await _reply_safe(update, "Usage: /task <text>")
        return

    agent_name = get_default_agent(chat.id)
    job_id = create_job(chat_id=chat.id, user_id=user.id, agent=agent_name, prompt=prompt)
    await _reply_safe(update, f"Job queued: {job_id} (agent: {agent_name})")


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return

    rows = list_recent_jobs(chat_id=update.effective_chat.id, limit=10)
    if not rows:
        await _reply_safe(update, "No jobs yet.")
        return

    lines: list[str] = []
    for row in rows:
        suffix = ""
        if row["status"] == "error" and row["error"]:
            suffix = f" error={str(row['error'])[:70]}"
        elif row["status"] == "done" and row["result"]:
            suffix = f" result={str(row['result'])[:70]}"
        elif row["status"] == "needs_approval":
            suffix = " waiting_for_approval=yes"

        lines.append(f"#{row['id']} {row['agent']} {row['status']}{suffix}")

    await _reply_safe(update, "Recent jobs:\n" + "\n".join(lines))


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return

    user = update.effective_user
    if not user:
        return

    if not context.args:
        await _reply_safe(update, "Usage: /approve <job_id>")
        return

    try:
        job_id = int(context.args[0])
    except ValueError:
        await _reply_safe(update, "job_id must be an integer")
        return

    ok = approve_job(job_id=job_id, approved_by=user.id)
    if ok:
        await _reply_safe(update, f"Job {job_id} approved and re-queued.")
    else:
        await _reply_safe(update, f"Job {job_id} is not awaiting approval.")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.error(
            "Telegram update polling conflict detected. Another bot instance with the same token is active."
        )
        return
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def _build_application() -> Application:
    if not SETTINGS.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(SETTINGS.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("agents", agents_command))
    app.add_handler(CommandHandler("use", use_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_error_handler(_on_error)
    return app


def main() -> None:
    configure_logging()
    init_db()
    _acquire_single_instance_lock()

    logger.info("Starting PicoClaw bot")
    app = _build_application()
    # Python 3.14 requires an explicitly set loop in the main thread.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
