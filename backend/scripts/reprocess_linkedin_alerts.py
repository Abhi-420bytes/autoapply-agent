"""Re-read LinkedIn alert emails that arrived before per-posting support: one job per
posting (deduped by LinkedIn job id). Old merged jobs get a note; nothing is deleted.

    docker compose exec worker python scripts/reprocess_linkedin_alerts.py [--emails N] [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from app.boards.linkedin import alert_postings, canonical_url, title_skipped
from app.db.session import get_sessionmaker
from app.models import Job, SenderRule, SourceEmail
from app.models.enums import JobSource, JobStatus
from app.services.settings_service import get_app_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", type=int, default=0, help="only the N most recent alerts")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    created = 0
    seen: set[str] = set()
    with get_sessionmaker()() as db:
        words = get_app_settings(db).skip_title_words
        emails = list(
            db.scalars(
                select(SourceEmail)
                .where(SourceEmail.sender.ilike("%linkedin.com"))
                .order_by(SourceEmail.received_at.desc())
            )
        )
        if args.emails:
            emails = emails[: args.emails]
        for e in emails:
            postings = alert_postings(e.body_html, e.body_text)
            rule = db.get(SenderRule, e.sender_rule_id) if e.sender_rule_id else None
            old = db.get(Job, e.job_id) if e.job_id else None
            for p in postings:
                if skip := title_skipped(p.title, words):
                    print(f"  - skipped ({skip}): {p.title}")
                    continue
                key = f"linkedin:{p.job_id}"
                if key in seen or db.scalar(select(Job.id).where(Job.dedupe_key == key)):
                    continue
                seen.add(key)
                print(f"  + {p.title or '(untitled)'}  {canonical_url(p.job_id)}")
                created += 1
                if args.dry_run:
                    continue
                db.add(
                    Job(
                        source=JobSource.EMAIL,
                        status=JobStatus.DETECTED,
                        role=p.title or None,
                        apply_mode=rule.apply_mode if rule else None,
                        dedupe_key=key,
                        portal_job_ref=key,
                        apply_url=canonical_url(p.job_id),
                        jd_text=p.snippet or p.title or None,
                        detected_at=datetime.now(UTC),
                        scheduled_at=datetime.now(UTC),
                    )
                )
            if old is not None and not (old.dedupe_key or "").startswith("linkedin:"):
                old.notes = "This LinkedIn alert listed several postings; each now has its own job."
        if not args.dry_run:
            db.commit()
    print(f"{'would create' if args.dry_run else 'created'} {created} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
