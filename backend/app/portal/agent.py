"""The portal agent (Playwright, headless Chromium).

Safety rules enforced here:
- Only allowlisted portal domains may be navigated to (the main frame is checked on every
  navigation; leaving the allowlist aborts the run).
- CAPTCHA and OTP are never bypassed: the agent pauses, shows the user a screenshot in the
  dashboard, and continues only with the answer the user types (or gives up).
- Applying requires an APPROVED application (or auto-apply + a passing resume) and explicit
  apply selectors in the portal config. The agent never guesses how to submit.
- Every step is screenshotted for the audit trail; failures notify the user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.email.ingest import attachment_text, email_jd_text, parse_deadline
from app.email.links import host_allowed
from app.generation.jd import Eligibility
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import Application, Job, JobFile, JobSite, Portal, PortalSkill, Resume, Setting
from app.models.enums import (
    ApplicationStatus,
    AuditActor,
    JobFileKind,
    JobStatus,
    LLMTask,
    NotificationLevel,
)
from app.portal.config import PortalConfig, load_config
from app.portal.learn import (
    FORM_FIELDS_JS,
    Episode,
    SkillBook,
    check_submitted,
    field_key,
    map_fields,
    profile_paths,
    propose_selector,
    seed,
)

if TYPE_CHECKING:
    from app.sites.search import DiscoverReport
from app.security import crypto
from app.services.notifications import notify
from app.services.settings_service import (
    get_app_settings,
    get_profile,
    resume_filename,
    set_profile,
)

log = logging.getLogger(__name__)
NAV_TIMEOUT_MS = 45_000
PROMPT_TIMEOUT_S = 600
PROMPT_POLL_S = 3.0
PROMPT_KEY = "portal.prompt.{job_id}"
SITE_PROMPT_KEY = "portal.prompt.site.{site_id}"


def prompt_key(subject: Job | JobSite) -> str:
    if isinstance(subject, JobSite):
        return SITE_PROMPT_KEY.format(site_id=subject.id)
    return PROMPT_KEY.format(job_id=subject.id)


class PortalError(Exception):
    """A run-stopping problem, already explained for the user."""


class HumanNeeded(PortalError):
    """OTP/CAPTCHA/credentials needed and the user didn't respond in time."""


class PortalJob(BaseModel):
    company: str | None = None
    role: str | None = None
    ctc: str | None = None
    location: str | None = None
    deadline: str | None = Field(default=None, description="ISO 8601 if stated")
    eligibility: Eligibility = Field(default_factory=Eligibility)
    required_documents: list[str] = Field(default_factory=list)


@dataclass
class ScrapeResult:
    jd_text: str
    fields: PortalJob
    attachments: int
    screenshots: int


def _data_root() -> Path:
    return Path(get_config().data_dir)


STEP_PROBE_MS = 1500
PROPOSALS_PER_STEP = 2
CONFIRM_WAIT_S = 15.0
STEP_NAMES = {
    "apply.open": "open the application form",
    "apply.resume_input": "attach the resume",
    "apply.submit": "submit the form",
}
_NOT_SUBMIT = re.compile(r"draft|cancel|withdraw|back|reset|delete|logout|sign out", re.I)


def _is_submit_button(loc: Any) -> bool:
    """Exploration safety: the submit arm must be a real submit-like button, never a
    draft/cancel/withdraw control."""
    info = loc.evaluate(
        "e => ({tag: e.tagName, type: e.type || '', role: e.getAttribute('role') || '',"
        " text: (e.innerText || e.value || '').trim()})"
    )
    is_button = (
        info["tag"] == "BUTTON"
        or info["role"] == "button"
        or (info["tag"] == "INPUT" and info["type"] in ("submit", "button"))
    )
    return bool(is_button and not _NOT_SUBMIT.search(info["text"]))


RESTORE_SESSION_JS = """(() => {
  const saved = %s[location.origin];
  if (!saved) return;
  for (const [k, v] of Object.entries(saved)) {
    if (sessionStorage.getItem(k) === null) sessionStorage.setItem(k, v);
  }
})();"""


def _session_storage(page: Any, portal: Portal) -> dict[str, dict[str, str]]:
    """The open tab's sessionStorage, if it's on the portal (kept with the saved session)."""
    try:
        if page.is_closed() or not _allowed(page.url, portal):
            return {}
        got = page.evaluate(
            "() => ({origin: location.origin,"
            " items: Object.fromEntries(Object.entries(sessionStorage))})"
        )
    except Exception:  # noqa: BLE001
        return {}
    return {got["origin"]: got["items"]} if got.get("items") else {}


def _allowed(url: str, portal: Portal) -> bool:
    u = urlparse(url)
    if u.scheme in ("about", "data", "blob"):
        return True
    local = u.hostname in ("127.0.0.1", "localhost")  # test fixtures only
    return (u.scheme == "https" or (local and u.scheme == "http")) and host_allowed(
        u.hostname or "", portal.allowed_domains
    )


CHALLENGE_WAIT_MS = 4000


def session_saved_at(portal: Portal) -> datetime | None:
    p = _session_path(portal)
    return datetime.fromtimestamp(p.stat().st_mtime, UTC) if p.exists() else None


def _session_path(portal: Portal) -> Path:
    return _data_root() / "portal_sessions" / f"{portal.id}.enc"


def _load_session(portal: Portal) -> dict[str, Any] | None:
    p = _session_path(portal)
    if not p.exists():
        return None
    try:
        data: dict[str, Any] = crypto.decrypt_json(p.read_text())
        return data
    except Exception:
        log.warning("portal session for %s unreadable; starting fresh", portal.name)
        return None


def clear_session(portal: Portal) -> None:
    _session_path(portal).unlink(missing_ok=True)


def save_session(portal: Portal, state: dict[str, Any]) -> None:
    p = _session_path(portal)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(crypto.encrypt_json(state))  # cookies are credentials: encrypted at rest


def profile_value(profile: dict[str, Any], path: str) -> str | None:
    cur: Any = profile
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return None if cur in (None, "") else str(cur)


class PortalAgent:
    def __init__(
        self,
        sessions: Callable[[], Session],
        gateway: LLMGateway,
        *,
        launch: Callable[[], Any] | None = None,
        prompt_timeout_s: float = PROMPT_TIMEOUT_S,
        config_root: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._launch = launch
        self._prompt_timeout = prompt_timeout_s
        self._config_root = config_root

    # -- browser ---------------------------------------------------------------------------

    @contextmanager
    def _browser(self, portal: Portal) -> Iterator[Any]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = self._launch() if self._launch else pw.chromium.launch(headless=True)
            saved = _load_session(portal) or {}
            session_storage: dict[str, dict[str, str]] = saved.pop("session_storage", None) or {}
            context = browser.new_context(
                storage_state=saved or None,  # type: ignore[arg-type]
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130 Safari/537.36"
                ),
            )
            context.set_default_timeout(NAV_TIMEOUT_MS)
            if session_storage:
                # Restore the user's sessionStorage (login tokens) before the portal's scripts run
                context.add_init_script(RESTORE_SESSION_JS % json.dumps(session_storage))
            page = context.new_page()
            violations: list[str] = []

            def block_off_domain(route: Any) -> None:
                req = route.request
                # Main-frame navigations are fetched here with redirects disabled, so an
                # off-allowlist target (direct or via a server redirect) is blocked BEFORE
                # the browser contacts it. Subresources/iframes (CDNs, captcha) pass through.
                if not (req.is_navigation_request() and req.frame.parent_frame is None):
                    route.continue_()
                    return
                if not _allowed(req.url, portal):
                    violations.append(req.url)
                    route.abort("blockedbyclient")
                    return
                if req.method != "GET":
                    # Form submissions go straight through: Chromium doesn't expose file
                    # bytes to the router, so proxying would drop the uploaded resume. The URL
                    # is already allowlisted; the post-navigation check covers redirects.
                    route.continue_()
                    return
                resp = route.fetch(max_redirects=0)
                location = resp.headers.get("location")
                if 300 <= resp.status < 400 and location:
                    target = urljoin(req.url, location)
                    if not _allowed(target, portal):
                        violations.append(target)
                        route.abort("blockedbyclient")
                        return
                route.fulfill(response=resp)

            context.route("**/*", block_off_domain)
            page._autoapply_violations = violations  # type: ignore[union-attr]
            try:
                yield page
            finally:
                try:
                    state = dict(context.storage_state(indexed_db=True))
                    state["session_storage"] = session_storage | _session_storage(page, portal)
                    save_session(portal, state)
                finally:
                    browser.close()

    def _check_nav(self, page: Any, portal: Portal | None = None) -> None:
        bad = list(getattr(page, "_autoapply_violations", []))
        if portal is not None and not _allowed(page.url, portal):
            bad.append(page.url)  # second layer: whatever actually loaded
        if bad:
            raise PortalError(
                f"the portal redirected to a non-allowed address ({bad[-1]}). Nothing was "
                "submitted. If this is your college SSO, add its domain to the portal's "
                "allowed domains."
            )

    def _goto(self, page: Any, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            self._check_nav(page)  # a blocked redirect surfaces as a navigation error
            raise PortalError(f"couldn't open {url}: {str(exc).splitlines()[0][:200]}") from exc
        self._check_nav(page)

    def _screenshot(
        self,
        db: Session,
        page: Any,
        job: Job,
        label: str,
        kind: JobFileKind = JobFileKind.SCREENSHOT,
    ) -> JobFile:
        folder = _data_root() / "jobs" / str(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now(UTC):%Y%m%dT%H%M%S}_{label}.png"
        page.screenshot(path=str(path), full_page=True)
        f = JobFile(
            job_id=job.id,
            kind=kind,
            path=str(path.relative_to(_data_root())),
            original_name=f"{label}.png",
            mime_type="image/png",
        )
        db.add(f)
        db.flush()
        return f

    # -- human-in-the-loop -----------------------------------------------------------------

    def _ask_human(self, page: Any, subject: Job | JobSite, kind: str, question: str) -> str:
        """Pause for an OTP/CAPTCHA answer typed by the user in the dashboard. The subject is
        the job being scraped/applied, or the job website being searched."""
        is_site = isinstance(subject, JobSite)
        key = prompt_key(subject)
        with self._sessions() as db:
            data: dict[str, Any] = {
                "kind": kind,
                "question": question,
                "answer": None,
                "asked_at": datetime.now(UTC).isoformat(),
            }
            if isinstance(subject, JobSite):
                folder = _data_root() / "sites" / str(subject.id)
                folder.mkdir(parents=True, exist_ok=True)
                shot_path = folder / f"needs_{kind}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                data["screenshot_path"] = str(shot_path.relative_to(_data_root()))
            else:
                data["screenshot_file_id"] = self._screenshot(db, page, subject, f"needs_{kind}").id
            existing = db.get(Setting, key)
            row = existing if existing is not None else Setting(key=key, is_secret=True)
            row.secret_value = json.dumps(data)
            db.add(row)
            if not is_site:
                j = db.get(Job, subject.id)
                if j is not None:
                    j.status_reason = f"Waiting for you: {question}"
            db.commit()
            notify(
                db,
                NotificationLevel.WARNING,
                "portal.human_needed",
                f"Action needed: {question}",
                ("Open Job websites" if is_site else "Open the job page")
                + " to answer. The agent waits up to 10 minutes and never tries to bypass "
                "OTP/CAPTCHA.",
                job_id=None if is_site else subject.id,
            )
        deadline = time.monotonic() + self._prompt_timeout
        while time.monotonic() < deadline:
            with self._sessions() as db:
                pending = db.get(Setting, key)
                got = json.loads(pending.secret_value) if pending and pending.secret_value else {}
                if got.get("answer") and pending is not None:
                    db.delete(pending)
                    if not is_site:
                        j = db.get(Job, subject.id)
                        if j is not None:
                            j.status_reason = None
                    db.commit()
                    return str(got["answer"])
            time.sleep(PROMPT_POLL_S)
        with self._sessions() as db:
            leftover = db.get(Setting, key)
            if leftover is not None:
                db.delete(leftover)
                db.commit()
        raise HumanNeeded(
            f"no answer to '{question}' within {int(self._prompt_timeout / 60)} "
            "minutes; run it again when you're available"
        )

    # -- login -------------------------------------------------------------------------------

    def _visible(self, page: Any, selectors: list[str] | str) -> bool:
        for sel in [selectors] if isinstance(selectors, str) else selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:  # noqa: BLE001, S112 — invalid/absent selector = not visible
                continue
        return False

    def _on_login_page(self, page: Any, cfg: PortalConfig) -> bool:
        path = urlparse(page.url).path.lower()
        return any(p in path for p in cfg.login.url_patterns) or self._visible(
            page, cfg.login.password
        )

    def _challenge_gate(self, page: Any, cfg: PortalConfig) -> bool:
        """A reCAPTCHA/hCaptcha check (click or image puzzle), not a typed text CAPTCHA.

        Those can only be solved by a person in a real browser window, so the agent stops
        and asks the user to sign in once themselves (their session is then reused).
        """
        if "captcha" in urlparse(page.url).path.lower():
            return True
        widget = "iframe[src*=recaptcha], iframe[src*=hcaptcha], .g-recaptcha, .h-captcha"
        try:
            page.wait_for_selector(widget, state="attached", timeout=CHALLENGE_WAIT_MS)
        except Exception:  # noqa: BLE001 — no widget appeared
            return False
        return not self._visible(page, cfg.login.captcha_input)

    def _login(
        self, page: Any, job: Job | JobSite, portal: Portal, cfg: PortalConfig, target: str
    ) -> None:
        if (
            self._on_login_page(page, cfg) or "captcha" in urlparse(page.url).path.lower()
        ) and self._challenge_gate(page, cfg):
            raise HumanNeeded(
                f'{portal.name} shows a CAPTCHA check (e.g. "I\'m not a robot") before '
                "login. The agent never solves CAPTCHAs, so sign in once yourself: run "
                f"`backend/.venv/bin/python backend/scripts/portal_login.py {portal.id}`, "
                "solve the CAPTCHA and log in in the window that opens, then press Enter. "
                "The agent reuses that session until it expires."
            )
        if not self._on_login_page(page, cfg):
            return
        creds = portal.credentials or {}
        if self._visible(page, cfg.login.captcha_indicators):
            answer = self._ask_human(
                page, job, "captcha", f"Solve the CAPTCHA to log in to {portal.name}"
            )
            page.locator(cfg.login.captcha_input).first.fill(answer)
        if not creds.get("username") or not creds.get("password"):
            raise HumanNeeded(
                f"{portal.name} needs a login. Add your portal username and "
                "password in Settings → Portals (stored encrypted)."
            )
        page.locator(cfg.login.username).first.fill(creds["username"])
        page.locator(cfg.login.password).first.fill(creds["password"])
        page.locator(cfg.login.submit).first.click()
        page.wait_for_load_state("domcontentloaded")
        if self._visible(page, cfg.login.otp_indicators):
            code = self._ask_human(page, job, "otp", f"Enter the OTP {portal.name} just sent you")
            page.locator(cfg.login.otp_input).first.fill(code)
            page.locator(cfg.login.code_submit).first.click()
            page.wait_for_load_state("domcontentloaded")
        self._check_nav(page)
        if self._on_login_page(page, cfg):
            raise PortalError(
                f"login to {portal.name} failed (still on the login page); check "
                "the saved username/password"
            )
        if page.url.rstrip("/") != target.rstrip("/"):
            self._goto(page, target)

    def _open(self, page: Any, job: Job, portal: Portal, cfg: PortalConfig) -> None:
        if not job.apply_url or not _allowed(job.apply_url, portal):
            raise PortalError("the job link isn't on an allowed portal domain; it was not opened")
        self._goto(page, job.apply_url)
        self._login(page, job, portal, cfg, job.apply_url)
        try:
            page.wait_for_selector(cfg.job.ready, timeout=15_000)
        except Exception as exc:  # noqa: BLE001
            raise PortalError(f"the job page didn't load as expected ({cfg.job.ready})") from exc

    def _context(self, job_id: int) -> tuple[Job, Portal, PortalConfig]:
        with self._sessions() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise PortalError(f"job {job_id} not found")
            portal = db.get(Portal, job.portal_id) if job.portal_id else None
            if portal is None:
                raise PortalError("this job has no portal configured")
            db.expunge(job)
            db.expunge(portal)
        return job, portal, load_config(portal.name, portal.selectors_config, self._config_root)

    # -- scrape ------------------------------------------------------------------------------

    def _text(self, page: Any, selector: str | None) -> str | None:
        if not selector:
            return None
        try:
            loc = page.locator(selector).first
            return loc.inner_text(timeout=3000).strip() if loc.count() else None
        except Exception:  # noqa: BLE001
            return None

    def scrape(self, job_id: int, *, merge_email: bool) -> ScrapeResult:
        job, portal, cfg = self._context(job_id)
        with self._browser(portal) as page:
            self._open(page, job, portal, cfg)
            with self._sessions() as db:
                self._screenshot(db, page, job, "job_page")
                db.commit()
            jd = self._text(page, cfg.job.description)
            if not jd:
                for sel in ("main", "article", "body"):
                    jd = self._text(page, sel)
                    if jd:
                        break
            jd = (jd or "")[:40_000]
            fields = self._extract_fields(page, cfg, jd, job_id)
            n_att = self._download_attachments(page, job, portal, cfg)

        with self._sessions() as db:
            j = db.get(Job, job_id)
            assert j is not None
            parts = [jd]
            for f in db.scalars(
                select(JobFile).where(
                    JobFile.job_id == j.id, JobFile.kind == JobFileKind.JD_ATTACHMENT
                )
            ):
                parts.append(
                    f"Attachment {f.original_name}:\n{attachment_text(_data_root() / f.path)}"
                )
            if merge_email:
                parts.insert(0, email_jd_text(db, j))
            j.jd_text = "\n\n".join(p for p in parts if p.strip())[:60_000]
            j.company = j.company or fields.company
            j.role = j.role or fields.role
            j.ctc = j.ctc or fields.ctc
            j.location = j.location or fields.location
            if not j.deadline and fields.deadline:
                j.deadline = parse_deadline(fields.deadline, get_app_settings(db).timezone)
            j.required_documents = fields.required_documents or j.required_documents
            j.status = JobStatus.SCRAPED
            j.status_reason = None
            db.commit()
        return ScrapeResult(jd_text=jd, fields=fields, attachments=n_att, screenshots=1)

    def _extract_fields(self, page: Any, cfg: PortalConfig, jd: str, job_id: int) -> PortalJob:
        direct = PortalJob(
            company=self._text(page, cfg.job.company),
            role=self._text(page, cfg.job.title),
            ctc=self._text(page, cfg.job.ctc),
            location=self._text(page, cfg.job.location),
            deadline=self._text(page, cfg.job.deadline),
        )
        try:
            llm = self._gateway.structured(
                LLMTask.PORTAL_HELPER,
                "portal_extract",
                {"page_text": jd[:15000]},
                PortalJob,
                job_id=job_id,
            ).value
        except LLMError as exc:
            log.warning("portal field extraction skipped: %s", exc)
            return direct
        # selectors win; the model only fills gaps
        return llm.model_copy(update={k: v for k, v in direct.model_dump().items() if v})

    def _download_attachments(self, page: Any, job: Job, portal: Portal, cfg: PortalConfig) -> int:
        count = 0
        folder = _data_root() / "jobs" / str(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        hrefs = page.eval_on_selector_all(cfg.job.attachments, "els => els.map(e => e.href)") or []
        for href in list(dict.fromkeys(hrefs))[:10]:
            if not _allowed(href, portal):
                continue  # never fetch attachments from other domains
            try:
                resp = page.context.request.get(href, timeout=30_000)
            except Exception as exc:  # noqa: BLE001
                log.info("attachment %s not downloaded: %s", href, exc)
                continue
            if not resp.ok or len(resp.body()) > 15_000_000:
                continue
            name = (
                re.sub(r"[^A-Za-z0-9._-]+", "_", Path(urlparse(href).path).name)[:120]
                or "attachment"
            )
            path = folder / f"portal_{count}_{name}"
            body = resp.body()
            path.write_bytes(body)
            with self._sessions() as db:
                db.add(
                    JobFile(
                        job_id=job.id,
                        kind=JobFileKind.JD_ATTACHMENT,
                        path=str(path.relative_to(_data_root())),
                        original_name=name,
                        mime_type=resp.headers.get("content-type"),
                        sha256=hashlib.sha256(body).hexdigest(),
                    )
                )
                db.commit()
            count += 1
        return count

    # -- apply -------------------------------------------------------------------------------

    def apply(self, job_id: int, application_id: int) -> None:
        job, portal, cfg = self._context(job_id)
        with self._sessions() as db:
            app_ = db.get(Application, application_id)
            if app_ is None or app_.job_id != job_id:
                raise PortalError("application not found")
            if app_.status is ApplicationStatus.SUBMITTED:
                return  # never submit twice
            if app_.status is not ApplicationStatus.APPROVED:
                raise PortalError("application isn't approved; nothing was submitted")
            resume = db.get(Resume, app_.resume_id) if app_.resume_id else None
            if resume is None or not resume.pdf_path:
                raise PortalError("the approved resume PDF is missing")
            pdf = _data_root() / resume.pdf_path
            upload_name = resume_filename(db)  # e.g. "Abhiram.pdf", not the stored file's name
            profile = get_profile(db).model_dump(mode="json")
        missing = [
            path for path in cfg.apply.fields.values() if profile_value(profile, path) is None
        ]
        if missing:
            raise PortalError(
                "your profile is missing values the form needs: "
                + ", ".join(missing)
                + ". Fill them in under Settings (nothing was guessed or submitted)."
            )

        with self._browser(portal) as page, self._sessions() as db:
            book = SkillBook(db, portal.id)
            episode = Episode()
            for role, sel in (
                ("apply.open", cfg.apply.open),
                ("apply.resume_input", cfg.apply.resume_input),
                ("apply.submit", cfg.apply.submit),
                ("apply.confirmation", cfg.apply.confirmation),
            ):
                seed(book, role, sel)
            db.commit()

            self._open(page, job, portal, cfg)
            if cfg.apply.open or not self._attached(page, "input[type=file]"):

                def open_form(loc: Any) -> None:
                    loc.click()
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_selector("input[type=file]", state="attached", timeout=10_000)

                self._learned_step(page, book, episode, "apply.open", open_form, job)
                self._check_nav(page)
            self._learned_step(
                page,
                book,
                episode,
                "apply.resume_input",
                lambda loc: loc.set_input_files(
                    {"name": upload_name, "mimeType": "application/pdf", "buffer": pdf.read_bytes()}
                ),
                job,
                visible=False,  # styled upload widgets hide the real <input type=file>
            )
            profile = self._fill_form(page, book, episode, cfg, profile, job, portal)
            with self._sessions() as sdb:
                self._screenshot(sdb, page, job, "before_submit")
                sdb.commit()
            if self._visible(page, cfg.login.captcha_indicators):
                answer = self._ask_human(
                    page, job, "captcha", "Solve the CAPTCHA to submit the application"
                )
                page.locator(cfg.login.captcha_input).first.fill(answer)
            submit = self._learned_step(
                page,
                book,
                episode,
                "apply.submit",
                lambda loc: loc.click(),
                job,
                guard=_is_submit_button,
            )
            confirmed = self._confirmed(page, book, episode, job)
            book.finish(episode, confirmed)
            if not confirmed:
                book.reward(submit, False)
                book.reward(submit, False)  # cancels its step reward: it didn't submit
            db.commit()
            if not confirmed:
                with self._sessions() as sdb:
                    self._screenshot(sdb, page, job, "submit_unconfirmed")
                    sdb.commit()
                raise PortalError(
                    "submitted, but the confirmation didn't appear; check the "
                    "screenshot and the portal before retrying"
                )
            self._check_nav(page)
            with self._sessions() as db:
                shot = self._screenshot(
                    db, page, job, "confirmation", JobFileKind.CONFIRMATION_SCREENSHOT
                )
                a = db.get(Application, application_id)
                j = db.get(Job, job_id)
                assert a is not None and j is not None
                a.status, a.submitted_at, a.confirmation_file_id = (
                    ApplicationStatus.SUBMITTED,
                    datetime.now(UTC),
                    shot.id,
                )
                j.status, j.status_reason = JobStatus.APPLIED, None
                db.commit()
                notify(
                    db,
                    NotificationLevel.INFO,
                    "job.applied",
                    f"Applied: {j.company} – {j.role}",
                    "Confirmation screenshot saved.",
                    job_id=j.id,
                )

    # -- learned apply steps (see app.portal.learn) ----------------------------------------

    def _attached(self, page: Any, selector: str) -> bool:
        try:
            return bool(page.locator(selector).count())
        except Exception:  # noqa: BLE001
            return False

    def _learned_step(
        self,
        page: Any,
        book: SkillBook,
        episode: Episode,
        role: str,
        act: Callable[[Any], None],
        job: Job,
        *,
        visible: bool = True,
        guard: Callable[[Any], bool] | None = None,
    ) -> PortalSkill:
        """Try the step's arms best-first (Thompson sampling); reward each outcome. When
        every known arm fails, the model proposes new ones from the live page."""
        tried: list[str] = []

        def attempt(arm: PortalSkill) -> bool:
            tried.append(arm.selector)
            try:
                loc = page.locator(arm.selector).first
                present = (
                    loc.is_visible(timeout=STEP_PROBE_MS)
                    if visible
                    else loc.count() > 0 or page.locator(arm.selector).count() > 0
                )
                if not present or (guard is not None and not guard(loc)):
                    ok = False
                else:
                    act(loc)
                    ok = True
            except Exception as exc:  # noqa: BLE001 — a failed arm is just a negative reward
                log.info("step %s via %r failed: %s", role, arm.selector, str(exc)[:160])
                ok = False
            book.reward(arm, ok, episode)
            book.db.commit()
            return ok

        for arm in book.ranked(role):
            if attempt(arm):
                return arm
        for _ in range(PROPOSALS_PER_STEP):
            sel = propose_selector(self._gateway, page, role, tried, job.id)
            if sel is None:
                break
            arm = book.arm(role, sel, source="model")
            if attempt(arm):
                return arm
        with self._sessions() as db:
            self._screenshot(db, page, job, f"stuck_{role.split('.')[-1]}")
            db.commit()
        raise PortalError(
            f"couldn't do the step '{STEP_NAMES.get(role, role)}' on this page (tried "
            f"{len(tried)} way(s)); see the screenshot. Nothing was submitted."
        )

    def _fill_form(
        self,
        page: Any,
        book: SkillBook,
        episode: Episode,
        cfg: PortalConfig,
        profile: dict[str, Any],
        job: Job,
        portal: Portal,
    ) -> dict[str, Any]:
        """Fill the form from the profile: config fields, then learned label → profile path
        mappings, then model mappings; anything required and still unknown is asked once
        and remembered. Returns the (possibly updated) profile."""
        done: set[str] = set()
        for selector, path in cfg.apply.fields.items():
            arm = book.arm("field", selector, source="config", key=None, value_path=path)
            self._fill(page.locator(selector).first, profile_value(profile, path) or "")
            book.reward(arm, True, episode)
            done.add(selector)
        for box in cfg.apply.checkboxes:
            page.locator(box).first.check()
            done.add(box)

        try:
            fields = [f for f in page.evaluate(FORM_FIELDS_JS) if f["selector"] not in done]
        except Exception:  # noqa: BLE001
            fields = []
        pending: list[dict[str, Any]] = []
        for f in fields:
            key = field_key(f["label"])
            if not key:
                continue
            if f["type"] == "checkbox":
                if f["required"] and not f["checked"]:
                    self._agree(page, book, episode, f, key, job)
                continue
            if f["type"] == "radio" or not f["empty"]:
                continue  # radio groups and portal-prefilled values are left as they are
            learned = book.field_path(key)
            value = profile_value(profile, learned) if learned else None
            if learned and value is not None:
                self._fill_learned(page, book, episode, f, key, learned, value, "learned")
            else:
                pending.append(f | {"key": key})

        if pending:
            mapped = map_fields(
                self._gateway,
                [f["label"] for f in pending],
                profile_paths(profile),
                job.id,
            )
            still: list[dict[str, Any]] = []
            for f in pending:
                guess = mapped.get(f["label"])
                value = profile_value(profile, guess) if guess else None
                if guess and value is not None:
                    self._fill_learned(page, book, episode, f, f["key"], guess, value, "model")
                elif f["required"]:
                    still.append(f)
            for f in still:
                answer = self._ask_human(
                    page,
                    job,
                    "question",
                    f'The {portal.name} form asks "{f["label"]}". What should I enter? '
                    "(Saved to your profile, so you won't be asked again.)",
                ).strip()
                profile = self._remember_answer(f["key"], answer)
                self._fill_learned(
                    page, book, episode, f, f["key"], f"form_fields.{f['key']}", answer, "user"
                )
        book.db.commit()
        return profile

    def _fill(self, loc: Any, value: str) -> None:
        if loc.evaluate("e => e.tagName") == "SELECT":
            try:
                loc.select_option(label=value)
            except Exception:  # noqa: BLE001
                loc.select_option(value=value)
        else:
            loc.fill(value)

    def _fill_learned(
        self,
        page: Any,
        book: SkillBook,
        episode: Episode,
        f: dict[str, Any],
        key: str,
        path: str,
        value: str,
        source: str,
    ) -> None:
        arm = book.arm("field", f["selector"], source=source, key=key, value_path=path)
        try:
            self._fill(page.locator(f["selector"]).first, value)
            book.reward(arm, True, episode)
        except Exception as exc:  # noqa: BLE001
            book.reward(arm, False)
            raise PortalError(f'couldn\'t fill "{f["label"]}": {str(exc)[:120]}') from exc

    def _agree(
        self, page: Any, book: SkillBook, episode: Episode, f: dict[str, Any], key: str, job: Job
    ) -> None:
        """Required checkbox (e.g. a declaration): tick only what the user agreed to."""
        if not book.agreed(key):
            answer = self._ask_human(
                page,
                job,
                "question",
                f'The form has a required checkbox: "{f["label"]}". Tick it for you? '
                "Answer yes or no (remembered for this portal).",
            )
            if answer.strip().lower() not in ("y", "yes", "ok", "agree", "i agree"):
                raise PortalError(f'you chose not to tick "{f["label"]}"; nothing was submitted')
        arm = book.arm("checkbox", f["selector"], source="user", key=key)
        page.locator(f["selector"]).first.check()
        book.reward(arm, True, episode)

    def _remember_answer(self, key: str, answer: str) -> dict[str, Any]:
        with self._sessions() as db:
            prof = get_profile(db)
            prof.form_fields[key] = answer
            set_profile(db, prof, AuditActor.AGENT)
            db.commit()
            return prof.model_dump(mode="json")

    def _confirmed(self, page: Any, book: SkillBook, episode: Episode, job: Job) -> bool:
        """Did the portal confirm the submission? Known confirmation arms first; otherwise
        the model reads the page (its quote must really be on the page) and the agent
        learns that confirmation for next time."""
        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            log.info("page still loading after submit: %s", str(exc)[:120])
        deadline = time.monotonic() + CONFIRM_WAIT_S
        while True:
            self._check_nav(page)
            for arm in book.ranked("apply.confirmation"):
                try:
                    if page.locator(arm.selector).first.is_visible(timeout=300):
                        book.reward(arm, True, episode)
                        return True
                except Exception:  # noqa: BLE001, S112 — invalid selector = not visible
                    continue
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(1000)
        try:
            text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            return False
        quote = check_submitted(self._gateway, text, job.id)
        if quote is None:
            return False
        arm = book.arm("apply.confirmation", f"text=/{re.escape(quote[:60])}/i", source="model")
        book.reward(arm, True, episode)
        return True

    # -- stage 1: searching a job website --------------------------------------------------

    def discover(self, site_id: int) -> DiscoverReport:
        from app.sites.search import (
            COLLECT_JS,
            DiscoverReport,
            clean_links,
            match_categories,
            pick_postings,
            polite_pause,
            record,
        )

        with self._sessions() as db:
            site = db.get(JobSite, site_id)
            if site is None:
                raise PortalError(f"job site {site_id} not found")
            portal = db.get(Portal, site.portal_id)
            if portal is None:
                raise PortalError("this job site has no portal")
            db.expunge(site)
            db.expunge(portal)
        cfg = load_config(portal.name, portal.selectors_config, self._config_root)
        report = DiscoverReport()
        postings: list[Any] = []
        with self._browser(portal) as page:
            for url in site.search_urls:
                if not _allowed(url, portal):
                    report.errors.append(f"{url} isn't on {portal.name}'s allowed domains")
                    continue
                self._goto(page, url)
                self._login(page, site, portal, cfg, url)
                for page_no in range(cfg.search.max_pages):
                    try:
                        page.wait_for_selector(cfg.search.ready, timeout=15_000)
                    except Exception as exc:  # noqa: BLE001
                        report.errors.append(f"results didn't load on {url}: {str(exc)[:120]}")
                        break
                    for _ in range(3):  # lazy-loaded result lists
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(600)
                    raw = page.eval_on_selector_all(cfg.search.job_link or "a[href]", COLLECT_JS)
                    links = clean_links(raw or [], lambda u: _allowed(u, portal))
                    report.pages += 1
                    report.links += len(links)
                    postings += pick_postings(
                        self._gateway, links, use_all=bool(cfg.search.job_link)
                    )
                    nxt = cfg.search.next_page
                    if (
                        not nxt
                        or page_no + 1 >= cfg.search.max_pages
                        or not self._visible(page, nxt)
                    ):
                        break
                    page.locator(nxt).first.click()
                    page.wait_for_load_state("domcontentloaded")
                    self._check_nav(page, portal)
                    polite_pause()
                polite_pause()
        unique = list({p.url: p for p in postings}.values())
        report.postings = len(unique)
        verdicts = match_categories(self._gateway, unique, site.categories, site.exclude_keywords)
        with self._sessions() as db:
            fresh = db.get(JobSite, site_id)
            assert fresh is not None
            record(db, fresh, unique, verdicts, report)
            fresh.last_checked_at = datetime.now(UTC)
            fresh.last_error = "; ".join(report.errors)[:1000] or None
            db.commit()
        return report
