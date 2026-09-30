"""Re-label job levels with the configured AI (display only: statuses are unchanged).

    docker compose exec worker python scripts/relabel_levels.py [--all]

Without --all only jobs whose level is "not stated" are re-checked.
"""

from __future__ import annotations

import argparse
import time

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.generation.level import label_job
from app.llm import get_gateway
from app.models import Job


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    gateway = get_gateway()
    with get_sessionmaker()() as db:
        stmt = select(Job.id).order_by(Job.id)
        if not args.all:
            stmt = stmt.where(Job.level.is_(None))
        ids = list(db.scalars(stmt))
    for job_id in ids:
        with get_sessionmaker()() as db:
            job = db.get(Job, job_id)
            if job is None or not (job.role or job.jd_text):
                continue
            level = label_job(job, gateway, db)
            db.commit()
            role = (job.role or "")[:40]
            print(f"{job_id:>4} {role:40} {level or 'not stated':11} {job.level_reason}")
        time.sleep(1)  # stay under the free tier's per-minute limit
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
