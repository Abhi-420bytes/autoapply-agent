# ruff: noqa: E501  (the in-page JavaScript snippets are kept readable, not wrapped)
"""The portal agent learns how to apply, one application at a time (reinforcement learning).

Every apply step ("apply.open", "apply.resume_input", "apply.submit", "apply.confirmation")
is a multi-armed bandit whose arms are ways to find the element: the portal config's
selector, built-in heuristics, and selectors the model proposed from the live page. Arms
are ordered by Thompson sampling from Beta(successes + 1, failures + 1), so what worked
before is tried first while untested arms still get a chance.

Rewards:
- step reward: +1 when the arm found a usable element and the action worked, -1 when it
  didn't (the next arm is then tried);
- episode reward: when the portal confirms the submission, every arm used in that episode
  gets a further +1; an unconfirmed submission costs the submit arm -1.

Form fields are learned as label → profile path. The value always comes from the user's
profile (never invented); a required field the profile can't answer is asked to the user
once, saved in profile.form_fields, and remembered for next time. Required checkboxes
(declarations) are ticked only after the user agreed to that exact label once.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import PortalSkill
from app.models.enums import LLMTask

log = logging.getLogger(__name__)

PRIOR_CONFIG = 2.0  # a hand-written config selector starts as if it had worked twice
MAX_ELEMENTS = 80

HEURISTICS: dict[str, list[str]] = {
    "apply.open": [
        "button:has-text('Apply Now')",
        "a:has-text('Apply Now')",
        "button:has-text('Apply')",
        "a:has-text('Apply')",
        "[role=button]:has-text('Apply')",
    ],
    "apply.resume_input": [
        "input[type=file][name*=resume i]",
        "input[type=file][accept*=pdf i]",
        "input[type=file]",
    ],
    "apply.submit": [
        "form:has(input[type=file]) button[type=submit]",
        "form:has(input[type=file]) input[type=submit]",
        "button:has-text('Submit')",
        "button[type=submit]",
        "input[type=submit]",
    ],
    "apply.confirmation": [
        "text=/applied successfully|successfully applied|application (has been )?"
        "(submitted|received)|thank you for applying/i",
    ],
}

# The DOM summary the model sees: interactive elements only, no values.
ELEMENTS_JS = """(max) => {
  const out = [];
  const label = (e) => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText; }
    const w = e.closest('label'); if (w) return w.innerText;
    return e.getAttribute('aria-label') || e.getAttribute('placeholder') || '';
  };
  for (const e of document.querySelectorAll('input,select,textarea,button,a[href],[role=button]')) {
    const r = e.getBoundingClientRect();
    if (e.type === 'hidden' || (r.width === 0 && r.height === 0)) continue;
    out.push({tag: e.tagName.toLowerCase(), type: e.type || '', id: e.id || '', name: e.name || '',
      label: (label(e) || '').trim().slice(0, 80),
      text: (['submit','button'].includes(e.type) && e.tagName === 'INPUT' ? e.value : (e.tagName === 'INPUT' ? '' : e.innerText || '')).trim().slice(0, 60),
      required: !!e.required});
    if (out.length >= max) break;
  }
  return out;
}"""

# Unfilled form controls on the apply form (for field learning).
FORM_FIELDS_JS = """() => {
  const label = (e) => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText; }
    const w = e.closest('label'); if (w) return w.innerText;
    return e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.name || e.id || '';
  };
  const sel = (e) => e.id ? `#${CSS.escape(e.id)}` : (e.name ? `${e.tagName.toLowerCase()}[name="${e.name}"]` : null);
  return [...document.querySelectorAll('input,select,textarea')].filter(e => {
    const r = e.getBoundingClientRect();
    return !['hidden','file','submit','button','image','reset'].includes(e.type) && r.width > 0 && !e.disabled;
  }).map(e => ({selector: sel(e), type: e.type, label: (label(e) || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
    required: !!e.required || /\\*\\s*$/.test(label(e) || ''), checked: !!e.checked, empty: !e.value}))
    .filter(f => f.selector);
}"""


class SelectorProposal(BaseModel):
    selector: str | None = Field(
        default=None, description="Playwright selector for the element, or null if absent"
    )
    reason: str = ""


class FieldMapping(BaseModel):
    label: str
    profile_path: str | None = Field(default=None, description="one of the allowed paths or null")


class FieldMappings(BaseModel):
    mappings: list[FieldMapping] = Field(default_factory=list)


class ConfirmationCheck(BaseModel):
    submitted: bool
    evidence: str = Field(default="", description="exact quote from the page text")


def field_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower().replace("*", "")).strip("_")[:80]


def profile_paths(profile: dict[str, Any]) -> list[str]:
    """Every scalar path in the profile a form field may be filled from."""
    out: list[str] = []

    def walk(prefix: str, v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else str(k), x)
        elif isinstance(v, list):
            for i, x in enumerate(v[:3]):
                walk(f"{prefix}.{i}", x)
        elif v not in (None, "", []):
            out.append(prefix)

    walk("", {k: v for k, v in profile.items() if k != "skills"})
    return out


@dataclass
class Episode:
    """What one application attempt used, for the end-of-episode reward."""

    used: list[int] = field(default_factory=list)  # PortalSkill ids


class SkillBook:
    """Per-portal learned skills, backed by the portal_skills table."""

    def __init__(self, db: Session, portal_id: int, rng: random.Random | None = None) -> None:
        self.db = db
        self.portal_id = portal_id
        self.rng = rng or random.Random()  # noqa: S311 — bandit sampling, not security

    # -- memory ----------------------------------------------------------------------------

    def arm(
        self,
        role: str,
        selector: str,
        *,
        source: str,
        key: str | None = None,
        value_path: str | None = None,
        prior: float = 0.0,
    ) -> PortalSkill:
        row = self.db.scalar(
            select(PortalSkill).where(
                PortalSkill.portal_id == self.portal_id,
                PortalSkill.role == role,
                PortalSkill.selector == selector,
            )
        )
        if row is None:
            row = PortalSkill(
                portal_id=self.portal_id,
                role=role,
                selector=selector,
                source=source,
                key=key,
                value_path=value_path,
                successes=prior,
                failures=0.0,
            )
            self.db.add(row)
            self.db.flush()
        elif value_path and row.value_path != value_path:
            row.value_path, row.key = value_path, key or row.key
        return row

    def arms(self, role: str) -> list[PortalSkill]:
        return list(
            self.db.scalars(
                select(PortalSkill).where(
                    PortalSkill.portal_id == self.portal_id, PortalSkill.role == role
                )
            )
        )

    def ranked(self, role: str) -> list[PortalSkill]:
        """Thompson sampling: order arms by a draw from each arm's Beta posterior."""
        return sorted(
            self.arms(role),
            key=lambda a: self.rng.betavariate(a.successes + 1, a.failures + 1),
            reverse=True,
        )

    def reward(self, arm: PortalSkill, ok: bool, episode: Episode | None = None) -> None:
        if ok:
            arm.successes += 1
            if episode is not None and arm.id not in episode.used:
                episode.used.append(arm.id)
        else:
            arm.failures += 1
        arm.last_used_at = datetime.now(UTC)
        self.db.flush()

    def finish(self, episode: Episode, success: bool) -> None:
        """Episode reward: a confirmed application reinforces every step that led to it."""
        if not success:
            return
        for arm_id in episode.used:
            arm = self.db.get(PortalSkill, arm_id)
            if arm is not None:
                arm.successes += 1
        self.db.flush()

    # -- fields ----------------------------------------------------------------------------

    def field_path(self, key: str) -> str | None:
        rows = [a for a in self.arms("field") if a.key == key and a.value_path]
        rows.sort(key=lambda a: a.successes - a.failures, reverse=True)
        return rows[0].value_path if rows else None

    def agreed(self, key: str) -> bool:
        return any(a.key == key and a.successes > a.failures for a in self.arms("checkbox"))


def seed(book: SkillBook, role: str, config_selector: str | None) -> None:
    if config_selector:
        book.arm(role, config_selector, source="config", prior=PRIOR_CONFIG)
    for sel in HEURISTICS.get(role, []):
        book.arm(role, sel, source="heuristic")


def page_elements(page: Any) -> list[dict[str, Any]]:
    try:
        return list(page.evaluate(ELEMENTS_JS, MAX_ELEMENTS))
    except Exception:  # noqa: BLE001
        return []


def propose_selector(
    gateway: LLMGateway, page: Any, role: str, tried: list[str], job_id: int | None
) -> str | None:
    """Ask the portal_helper model for a selector for `role`, from the page's elements."""
    elements = page_elements(page)
    if not elements:
        return None
    try:
        out = gateway.structured(
            LLMTask.PORTAL_HELPER,
            "portal_learn",
            {
                "task": "selector",
                "role": role,
                "url": page.url,
                "elements": elements,
                "tried": tried,
            },
            SelectorProposal,
            job_id=job_id,
        ).value
    except LLMError as exc:
        log.warning("selector proposal for %s failed: %s", role, exc)
        return None
    sel = (out.selector or "").strip()
    return sel if sel and sel not in tried and len(sel) <= 500 else None


def map_fields(
    gateway: LLMGateway, labels: list[str], paths: list[str], job_id: int | None
) -> dict[str, str | None]:
    """Model maps unknown form labels to allowed profile paths (or none)."""
    if not labels:
        return {}
    try:
        out = gateway.structured(
            LLMTask.PORTAL_HELPER,
            "portal_learn",
            {"task": "fields", "labels": labels, "paths": paths},
            FieldMappings,
            job_id=job_id,
        ).value
    except LLMError as exc:
        log.warning("field mapping failed: %s", exc)
        return {}
    allowed = set(paths)
    return {m.label: (m.profile_path if m.profile_path in allowed else None) for m in out.mappings}


def check_submitted(gateway: LLMGateway, page_text: str, job_id: int | None) -> str | None:
    """Did the page confirm the submission? Returns the verified quote, or None."""
    try:
        out = gateway.structured(
            LLMTask.PORTAL_HELPER,
            "portal_learn",
            {"task": "confirmation", "page_text": page_text[:6000]},
            ConfirmationCheck,
            job_id=job_id,
        ).value
    except LLMError as exc:
        log.warning("confirmation check failed: %s", exc)
        return None
    quote = " ".join(out.evidence.split())
    if out.submitted and len(quote) >= 8 and quote.lower() in " ".join(page_text.split()).lower():
        return quote
    return None
