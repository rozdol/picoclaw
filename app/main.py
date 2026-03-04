from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import platform
from pathlib import Path
from typing import TextIO

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import SETTINGS, configure_logging
from app.db import (
    add_chat_memory,
    approve_job,
    clear_chat_memory,
    create_job,
    delete_chat_memory,
    get_default_agent,
    list_chat_memory,
    get_enabled_skills_for_chat,
    init_db,
    list_recent_jobs,
    list_skills_with_chat_state,
    set_chat_skill_enabled,
    set_default_agent,
    upsert_skill,
)
from app.router import available_agents, is_valid_agent, run_agent
from app.security import is_user_allowed
from app.skills import build_skill_system_prompt

logger = logging.getLogger(__name__)
_BOT_LOCK_HANDLE: TextIO | None = None
_BOT_LOCK_PATH = Path("/tmp/picoclaw-bot.lock")
_MAX_MEMORY_ITEM_LENGTH = 500
_MEMORY_PROMPT_ITEM_LIMIT = 20


def _read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _load_os_pretty_name() -> str:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", maxsplit=1)[1].strip().strip('"')
    except OSError:
        return ""
    return ""


def _load_meminfo_kb() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for raw_line in handle:
                key, _, value = raw_line.partition(":")
                if not key or not value:
                    continue
                amount = value.strip().split()[0]
                if amount.isdigit():
                    info[key] = int(amount)
    except OSError:
        return {}
    return info


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _format_uptime(seconds: float) -> str:
    total_seconds = int(seconds)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _build_device_report() -> str:
    os_name = _load_os_pretty_name() or platform.system()
    kernel = platform.release() or "unknown"
    hostname = platform.node() or "unknown"
    machine = platform.machine() or "unknown"
    model = _read_first_line("/proc/device-tree/model") or "unknown"
    python_version = platform.python_version()
    cpu_cores = os.cpu_count() or 0

    meminfo = _load_meminfo_kb()
    ram_total_kb = meminfo.get("MemTotal", 0)
    ram_available_kb = meminfo.get("MemAvailable", 0)
    ram_used_kb = max(ram_total_kb - ram_available_kb, 0)
    ram_pct = (ram_used_kb / ram_total_kb * 100) if ram_total_kb else 0.0

    swap_total_kb = meminfo.get("SwapTotal", 0)
    swap_free_kb = meminfo.get("SwapFree", 0)
    swap_used_kb = max(swap_total_kb - swap_free_kb, 0)

    uptime_line = _read_first_line("/proc/uptime")
    uptime_seconds = 0.0
    if uptime_line:
        try:
            uptime_seconds = float(uptime_line.split()[0])
        except (ValueError, IndexError):
            uptime_seconds = 0.0

    try:
        load_1, load_5, load_15 = os.getloadavg()
        load_text = f"{load_1:.2f}, {load_5:.2f}, {load_15:.2f}"
    except OSError:
        load_text = "n/a"

    try:
        fs = os.statvfs("/")
        disk_total = fs.f_frsize * fs.f_blocks
        disk_free = fs.f_frsize * fs.f_bavail
        disk_used = max(disk_total - disk_free, 0)
        disk_pct = (disk_used / disk_total * 100) if disk_total else 0.0
        disk_text = (
            f"{_format_bytes(disk_used)}/{_format_bytes(disk_total)} "
            f"({disk_pct:.1f}% used)"
        )
    except OSError:
        disk_text = "n/a"

    lines = [
        "Device info:",
        f"host: {hostname}",
        f"model: {model}",
        f"os: {os_name}",
        f"kernel: {kernel}",
        f"arch: {machine}",
        f"python: {python_version}",
        f"cpu_cores: {cpu_cores}",
        f"load_avg(1,5,15): {load_text}",
        f"uptime: {_format_uptime(uptime_seconds)}",
        (
            "ram: "
            f"{_format_bytes(ram_used_kb * 1024)}/{_format_bytes(ram_total_kb * 1024)} "
            f"({ram_pct:.1f}% used)"
        ),
        f"swap: {_format_bytes(swap_used_kb * 1024)}/{_format_bytes(swap_total_kb * 1024)}",
        f"disk(/): {disk_text}",
    ]
    return "\n".join(lines)


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
        "/device - show host, OS, RAM, uptime and load info\n"
        "/whoami - show Telegram IDs and auth state\n"
        "/agents - list available agents\n"
        "/use <agent> - set default agent for this chat\n"
        "/ask <text> - run synchronous agent request\n"
        "or send plain text to chat naturally with the selected agent\n"
        "/task <text> - enqueue async job\n"
        "/jobs - list recent jobs\n"
        "/approve <job_id> - approve waiting ops job\n"
        "/skills - list available skills for this chat\n"
        "/skill_add <name> | <instructions> - create or update and enable skill\n"
        "/skill_enable <name> - enable skill for this chat\n"
        "/skill_disable <name> - disable skill for this chat\n"
        "MEMORY <text> - save a persistent chat memory\n"
        "MEMORY LIST - show memories\n"
        "MEMORY DELETE <id> - delete one memory\n"
        "MEMORY CLEAR - delete all chat memories"
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


async def device_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    await _reply_safe(update, _build_device_report())


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


def _load_skill_context(chat_id: int) -> tuple[str, int]:
    skills = get_enabled_skills_for_chat(chat_id)
    return build_skill_system_prompt(skills), len(skills)


def _load_memory_context(chat_id: int) -> tuple[str, int]:
    rows = list_chat_memory(chat_id, limit=_MEMORY_PROMPT_ITEM_LIMIT)
    if not rows:
        return "", 0

    lines = [
        "Persistent chat memory:",
        "Use these as long-term user/chat facts unless corrected by newer user input.",
    ]
    for row in reversed(rows):
        lines.append(f"- {str(row['content'])}")
    return "\n".join(lines), len(rows)


def _load_chat_context(chat_id: int) -> tuple[str, int, int]:
    skill_context, skill_count = _load_skill_context(chat_id)
    memory_context, memory_count = _load_memory_context(chat_id)

    parts = [part for part in (skill_context, memory_context) if part.strip()]
    if not parts:
        return "", 0, 0
    return "\n\n".join(parts), skill_count, memory_count


def _parse_memory_command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if not stripped.upper().startswith("MEMORY"):
        return None
    if len(stripped) > 6 and stripped[6] not in {" ", ":"}:
        return None

    rest = stripped[6:].strip()
    if rest.startswith(":"):
        rest = rest[1:].strip()

    if not rest:
        return ("help", "")

    upper_rest = rest.upper()
    if upper_rest in {"LIST", "SHOW"}:
        return ("list", "")
    if upper_rest == "CLEAR":
        return ("clear", "")
    if upper_rest.startswith("DELETE "):
        return ("delete", rest.split(maxsplit=1)[1].strip())
    if upper_rest.startswith("DEL "):
        return ("delete", rest.split(maxsplit=1)[1].strip())
    if upper_rest.startswith("ADD "):
        return ("add", rest[4:].strip())
    return ("add", rest)


async def _handle_memory_command(update: Update, prompt: str) -> bool:
    parsed = _parse_memory_command(prompt)
    if parsed is None:
        return False

    if not _is_authorized(update):
        await _deny(update)
        return True
    if not update.effective_chat:
        return True

    action, value = parsed
    chat_id = update.effective_chat.id

    if action == "help":
        await _reply_safe(
            update,
            "Memory usage:\n"
            "MEMORY <text>\n"
            "MEMORY LIST\n"
            "MEMORY DELETE <id>\n"
            "MEMORY CLEAR",
        )
        return True

    if action == "list":
        rows = list_chat_memory(chat_id, limit=50)
        if not rows:
            await _reply_safe(update, "No chat memory saved yet.")
            return True

        lines = ["Chat memory:"]
        for row in reversed(rows):
            lines.append(f"- {row['id']}: {row['content']}")
        await _reply_safe(update, "\n".join(lines))
        return True

    if action == "clear":
        deleted_count = clear_chat_memory(chat_id)
        await _reply_safe(update, f"Cleared {deleted_count} memory item(s).")
        return True

    if action == "delete":
        try:
            memory_id = int(value)
        except ValueError:
            await _reply_safe(update, "Usage: MEMORY DELETE <id>")
            return True

        if delete_chat_memory(chat_id, memory_id):
            await _reply_safe(update, f"Memory {memory_id} deleted.")
        else:
            await _reply_safe(update, f"Memory {memory_id} not found.")
        return True

    if action == "add":
        content = value.strip()
        if not content:
            await _reply_safe(update, "Usage: MEMORY <text>")
            return True

        truncated = False
        if len(content) > _MAX_MEMORY_ITEM_LENGTH:
            content = content[:_MAX_MEMORY_ITEM_LENGTH]
            truncated = True

        memory_id = add_chat_memory(chat_id, content)
        suffix = " (truncated)" if truncated else ""
        await _reply_safe(update, f"Saved memory {memory_id}{suffix}.")
        return True

    return False


async def _run_sync_prompt(update: Update, prompt: str) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return

    if not prompt:
        return

    chat_id = update.effective_chat.id
    agent_name = get_default_agent(chat_id)
    chat_context, skill_count, memory_count = _load_chat_context(chat_id)
    suffix_parts = []
    if skill_count:
        suffix_parts.append(f"skills={skill_count}")
    if memory_count:
        suffix_parts.append(f"memory={memory_count}")
    suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
    await _reply_safe(update, f"Running `{agent_name}` synchronously{suffix}...")

    try:
        result = await run_agent(agent_name, prompt, extra_system=chat_context)
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
    if await _handle_memory_command(update, prompt):
        return
    await _run_sync_prompt(update, prompt)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    prompt = (update.message.text or "").strip()
    if not prompt:
        return

    if await _handle_memory_command(update, prompt):
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
    if await _handle_memory_command(update, prompt):
        return

    agent_name = get_default_agent(chat.id)
    chat_context, skill_count, memory_count = _load_chat_context(chat.id)
    job_id = create_job(
        chat_id=chat.id,
        user_id=user.id,
        agent=agent_name,
        prompt=prompt,
        skill_context=chat_context,
    )
    suffix_parts = []
    if skill_count:
        suffix_parts.append(f"skills={skill_count}")
    if memory_count:
        suffix_parts.append(f"memory={memory_count}")
    suffix = f", {', '.join(suffix_parts)}" if suffix_parts else ""
    await _reply_safe(update, f"Job queued: {job_id} (agent: {agent_name}{suffix})")


def _parse_skill_add_args(raw_args: list[str]) -> tuple[str, str] | None:
    raw = " ".join(raw_args).strip()
    if "|" not in raw:
        return None
    name_part, content_part = raw.split("|", 1)
    name = name_part.strip().lower()
    content = content_part.strip()
    if not name or not content:
        return None
    return name, content


async def skills_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return

    rows = list_skills_with_chat_state(update.effective_chat.id)
    if not rows:
        await _reply_safe(update, "No skills defined. Use /skill_add <name> | <instructions>")
        return

    lines = []
    for row in rows:
        state = "enabled" if int(row["is_enabled"]) == 1 else "disabled"
        lines.append(f"- {row['name']} ({state})")

    await _reply_safe(update, "Skills:\n" + "\n".join(lines))


async def skill_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return

    parsed = _parse_skill_add_args(context.args)
    if parsed is None:
        await _reply_safe(update, "Usage: /skill_add <name> | <instructions>")
        return

    name, content = parsed
    try:
        _, created = upsert_skill(name, content)
    except ValueError as exc:
        await _reply_safe(update, f"Invalid skill: {exc}")
        return

    set_chat_skill_enabled(update.effective_chat.id, name, enabled=True)
    state = "created" if created else "updated"
    await _reply_safe(update, f"Skill `{name}` {state} and enabled for this chat.")


async def skill_enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return
    if not context.args:
        await _reply_safe(update, "Usage: /skill_enable <name>")
        return

    name = context.args[0].strip().lower()
    if not name:
        await _reply_safe(update, "Usage: /skill_enable <name>")
        return

    if not set_chat_skill_enabled(update.effective_chat.id, name, enabled=True):
        await _reply_safe(update, f"Unknown skill: {name}. Use /skills to list available skills.")
        return
    await _reply_safe(update, f"Skill `{name}` enabled for this chat.")


async def skill_disable_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _deny(update)
        return
    if not update.effective_chat:
        return
    if not context.args:
        await _reply_safe(update, "Usage: /skill_disable <name>")
        return

    name = context.args[0].strip().lower()
    if not name:
        await _reply_safe(update, "Usage: /skill_disable <name>")
        return

    if not set_chat_skill_enabled(update.effective_chat.id, name, enabled=False):
        await _reply_safe(update, f"Unknown skill: {name}. Use /skills to list available skills.")
        return
    await _reply_safe(update, f"Skill `{name}` disabled for this chat.")


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
    app.add_handler(CommandHandler("device", device_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("agents", agents_command))
    app.add_handler(CommandHandler("use", use_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("skills", skills_command))
    app.add_handler(CommandHandler("skill_add", skill_add_command))
    app.add_handler(CommandHandler("skill_enable", skill_enable_command))
    app.add_handler(CommandHandler("skill_disable", skill_disable_command))
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
