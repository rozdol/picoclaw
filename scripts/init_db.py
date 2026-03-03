import logging

from app.config import configure_logging
from app.db import get_db_journal_mode, init_db


def main() -> None:
    configure_logging()
    init_db()
    mode = get_db_journal_mode()
    logging.getLogger(__name__).info("Database initialized. journal_mode=%s", mode)


if __name__ == "__main__":
    main()
