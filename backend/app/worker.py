"""Background worker: runs scheduled/delayed jobs.

Uses a persistent job store so delayed jobs survive restarts. Registered jobs:
- heartbeat (5 min)
- embedding re-index poller (1 min): runs a full re-index after the embedding model changes
- knowledge poller (1 min): runs requested GitHub syncs, embeds new/changed items
- weekly GitHub sync (Sunday 03:00 UTC)
- resume generation runner (15 s): executes queued pipeline runs (LangGraph, checkpointed)
- email (1 min): polls inboxes that are due (or requested), then hands due jobs onward
- portal agent (15 s): scrape job pages and submit approved applications (Playwright)
- job websites (1 min): search each site that's due (stage 1); matches become jobs that the
  email job's due-processing hands to stage 2 (scrape → resume → approval → apply)
"""

from __future__ import annotations

import contextlib
import logging
import signal
from datetime import UTC, datetime
from types import FrameType

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.config import get_config
from app.db.session import get_engine, get_sessionmaker
from app.generation.pipeline import Deps
from app.generation.runs import checkpointer_for, recover_interrupted, run_next
from app.knowledge.jobs import request_github_sync, run_embedding_sync, run_pending_github_sync
from app.llm import get_gateway
from app.llm.reindex import run_pending_reindex
from app.security.redaction import configure_logging
from app.services.settings_service import get_app_settings

log = logging.getLogger("app.worker")


def heartbeat() -> None:
    log.info("worker heartbeat %s", datetime.now(UTC).isoformat(timespec="seconds"))


def process_reindex() -> None:
    try:
        run_pending_reindex(get_gateway(), get_sessionmaker())
    except Exception:
        log.exception("embedding re-index failed; will retry next run")


_checkpointer: object | None = None


def process_generation() -> None:
    from app.api.templates import get_compiler

    if _checkpointer is None:
        return
    try:
        deps = Deps(gateway=get_gateway(), sessions=get_sessionmaker(), compiler=get_compiler())
        while run_next(deps, _checkpointer) is not None:
            pass
    except Exception:
        log.exception("generation runner failed; will retry next run")


def process_email() -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from app.api.email import SYNC_REQUEST_PREFIX
    from app.email.ingest import poll_account, process_due_jobs
    from app.email.links import LinkResolver
    from app.email.oauth import TokenStore
    from app.email.providers import provider_for
    from app.generation.runs import queue_generation, queue_portal
    from app.models import EmailAccount, Setting

    resolver = LinkResolver()
    try:
        with get_sessionmaker()() as db:
            every = timedelta(minutes=get_app_settings(db).email_poll_minutes)
            now = datetime.now(UTC)
            for account in db.scalars(select(EmailAccount).where(EmailAccount.enabled.is_(True))):
                req = db.get(Setting, f"{SYNC_REQUEST_PREFIX}{account.id}")
                last = account.last_sync_at
                due = (
                    last is None
                    or now - (last if last.tzinfo else last.replace(tzinfo=UTC)) >= every
                )
                if not (req or due):
                    continue
                if req:
                    db.delete(req)
                    db.commit()
                report = poll_account(
                    db, get_gateway(), account, provider_for(account, TokenStore(db)), resolver
                )
                if report.jobs_created or report.errors:
                    log.info("email %s: %s", account.address, report)
            process_due_jobs(db, queue_generation, queue_portal, get_gateway())
    except Exception:
        log.exception("email job failed; will retry next run")
    finally:
        resolver.close()


def process_portal() -> None:
    from app.generation.runs import release_due_auto_submits, run_next_portal
    from app.portal.agent import PortalAgent

    try:
        with get_sessionmaker()() as db:
            release_due_auto_submits(db)  # review windows that ended → apply
        agent = PortalAgent(get_sessionmaker(), get_gateway())
        while run_next_portal(agent, get_sessionmaker()) is not None:
            pass
    except Exception:
        log.exception("portal runner failed; will retry next run")


def process_sites() -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from app.models import JobSite
    from app.models.enums import NotificationLevel
    from app.portal.agent import HumanNeeded, PortalAgent, PortalError
    from app.services.notifications import notify

    agent = PortalAgent(get_sessionmaker(), get_gateway())
    with get_sessionmaker()() as db:
        now = datetime.now(UTC)
        due = [
            s.id
            for s in db.scalars(select(JobSite).where(JobSite.enabled.is_(True)))
            if s.last_checked_at is None
            or now
            - (
                s.last_checked_at
                if s.last_checked_at.tzinfo
                else s.last_checked_at.replace(tzinfo=UTC)
            )
            >= timedelta(minutes=s.check_every_minutes)
        ]
    for site_id in due:
        try:
            report = agent.discover(site_id)
            log.info("site %s: %s", site_id, report)
        except (PortalError, HumanNeeded) as exc:
            with get_sessionmaker()() as db:
                site = db.get(JobSite, site_id)
                if site is not None:
                    site.last_checked_at, site.last_error = datetime.now(UTC), str(exc)[:1000]
                    db.commit()
                    notify(
                        db,
                        NotificationLevel.WARNING,
                        "site.search",
                        f"Searching {site.name} stopped",
                        str(exc)[:500],
                    )
        except Exception:
            log.exception("searching site %s failed", site_id)
            with get_sessionmaker()() as db:
                site = db.get(JobSite, site_id)
                if site is not None:
                    site.last_checked_at = datetime.now(UTC)
                    site.last_error = "unexpected error while searching (see worker logs)"
                    db.commit()


def process_outreach() -> None:
    """Cold-email outreach: discover, research, tailor, draft; send only what you approved."""
    from app.generation.runs import queue_generation
    from app.outreach.service import process_outreach as tick

    try:
        report = tick(get_gateway(), get_sessionmaker(), queue_generation)
        if report:
            log.info("outreach: %s", report)
    except Exception:
        log.exception("outreach tick failed; will retry")


def process_knowledge() -> None:
    try:
        run_pending_github_sync(get_gateway(), get_sessionmaker())
        run_embedding_sync(get_gateway(), get_sessionmaker())
    except Exception:
        log.exception("knowledge job failed; will retry next run")


def weekly_github_sync() -> None:
    with get_sessionmaker()() as db:
        if get_app_settings(db).github_username:
            request_github_sync(db, reason="weekly")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=get_engine(), tablename="scheduler_jobs")},
        job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1},
        timezone="UTC",
    )
    scheduler.add_job(
        heartbeat, "interval", minutes=5, id="heartbeat", replace_existing=True, jobstore="default"
    )
    scheduler.add_job(
        process_reindex, "interval", minutes=1, id="embedding_reindex", replace_existing=True
    )
    scheduler.add_job(
        process_knowledge, "interval", minutes=1, id="knowledge", replace_existing=True
    )
    scheduler.add_job(
        weekly_github_sync,
        "cron",
        day_of_week="sun",
        hour=3,
        id="weekly_github_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        process_generation, "interval", seconds=15, id="generation", replace_existing=True
    )
    scheduler.add_job(process_email, "interval", minutes=1, id="email", replace_existing=True)
    scheduler.add_job(process_portal, "interval", seconds=15, id="portal", replace_existing=True)
    scheduler.add_job(process_sites, "interval", minutes=1, id="sites", replace_existing=True)
    scheduler.add_job(process_outreach, "interval", minutes=2, id="outreach", replace_existing=True)
    return scheduler


def main() -> None:
    global _checkpointer
    configure_logging(get_config().log_level)
    stack = contextlib.ExitStack()
    _checkpointer = stack.enter_context(checkpointer_for(get_config().database_url))
    with get_sessionmaker()() as db:
        if n := recover_interrupted(db):
            log.info("re-queued %d interrupted generation run(s)", n)
    scheduler = build_scheduler()

    def _stop(signum: int, _frame: FrameType | None) -> None:
        log.info("received signal %s, shutting down", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker starting")
    try:
        scheduler.start()
    finally:
        stack.close()


if __name__ == "__main__":
    main()
