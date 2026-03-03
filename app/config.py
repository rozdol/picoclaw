import logging
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for partially provisioned envs
    def load_dotenv() -> bool:
        return False

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    db_path: str
    allowed_user_ids: frozenset[int]
    llm_provider: str
    openai_api_key: str
    openai_model: str
    openrouter_api_key: str
    openrouter_model: str
    llm_timeout_seconds: float
    require_approval_for_ops: bool
    worker_poll_interval_seconds: float
    log_level: str
    max_telegram_message_length: int


def _parse_allowed_users(raw_value: str) -> frozenset[int]:
    if not raw_value.strip():
        return frozenset()

    allowed: set[int] = set()
    for token in raw_value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            allowed.add(int(token))
        except ValueError:
            logging.getLogger(__name__).warning("Ignoring invalid ALLOWED_USER_IDS entry: %s", token)
    return frozenset(allowed)


def _parse_bool(raw_value: str, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        db_path=os.getenv("DB_PATH", "./picoclaw.db").strip(),
        allowed_user_ids=_parse_allowed_users(os.getenv("ALLOWED_USER_IDS", "")),
        llm_provider=os.getenv("LLM_PROVIDER", "openai").strip().lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip(),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "45")),
        require_approval_for_ops=_parse_bool(os.getenv("REQUIRE_APPROVAL_FOR_OPS", "0"), default=False),
        worker_poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        max_telegram_message_length=int(os.getenv("MAX_TELEGRAM_MESSAGE_LENGTH", "4000")),
    )


SETTINGS = load_settings()


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, SETTINGS.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Avoid leaking bot token in httpx request URLs at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
