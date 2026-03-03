from __future__ import annotations

import asyncio
import logging

from app.config import SETTINGS, configure_logging
from app.db import claim_oldest_queued_job, init_db, mark_job_done, mark_job_error, mark_job_needs_approval
from app.router import run_agent

logger = logging.getLogger(__name__)


async def _process_one_job() -> bool:
    job = claim_oldest_queued_job()
    if job is None:
        return False

    job_id = int(job["id"])
    agent = str(job["agent"])
    prompt = str(job["prompt"])
    is_approved = bool(job["is_approved"])

    if SETTINGS.require_approval_for_ops and agent == "ops" and not is_approved:
        mark_job_needs_approval(job_id)
        logger.info("Job %s moved to needs_approval", job_id)
        return True

    try:
        result = await run_agent(agent, prompt)
        mark_job_done(job_id, result)
        logger.info("Job %s completed", job_id)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        mark_job_error(job_id, str(exc)[:500])

    return True


async def run_worker_loop() -> None:
    init_db()
    poll_interval = max(0.5, SETTINGS.worker_poll_interval_seconds)

    logger.info("Worker started (poll_interval=%.2fs)", poll_interval)
    while True:
        try:
            found_job = await _process_one_job()
            if not found_job:
                await asyncio.sleep(poll_interval)
        except Exception:
            logger.exception("Worker loop error")
            await asyncio.sleep(poll_interval)


def main() -> None:
    configure_logging()
    asyncio.run(run_worker_loop())


if __name__ == "__main__":
    main()
