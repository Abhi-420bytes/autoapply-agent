"""Per-portal selector configuration (YAML), so broken selectors are a config fix.

Files live in backend/portal_configs/<slug>.yaml (mounted read-only into the containers;
edit and the next run picks them up). Anything not set falls back to default.yaml.
Selectors are Playwright selectors (CSS, `text=...`, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parents[2] / "portal_configs"


class LoginConfig(BaseModel):
    url_patterns: list[str] = Field(
        default_factory=lambda: ["/login", "/signin", "/sign-in", "/auth"]
    )
    username: str = (
        "input[type=email], input[name*=user i], input[name*=email i], input[id*=user i]"
    )
    password: str = "input[type=password]"  # noqa: S105 — a CSS selector
    submit: str = "button[type=submit], input[type=submit]"
    otp_indicators: list[str] = Field(
        default_factory=lambda: [
            "input[autocomplete=one-time-code]",
            "input[name*=otp i]",
            "text=/one[- ]time password|\\bOTP\\b/i",
        ]
    )
    otp_input: str = "input[autocomplete=one-time-code], input[name*=otp i], input[id*=otp i]"
    captcha_indicators: list[str] = Field(
        default_factory=lambda: [
            "iframe[src*=recaptcha]",
            "iframe[src*=hcaptcha]",
            ".g-recaptcha",
            ".h-captcha",
            "img[src*=captcha i]",
            "input[name*=captcha i]",
        ]
    )
    captcha_input: str = "input[name*=captcha i]"
    code_submit: str = "button[type=submit], input[type=submit]"


class JobPageConfig(BaseModel):
    ready: str = "body"  # wait for this before reading
    title: str | None = None
    company: str | None = None
    description: str | None = None  # None → main/article/body text
    ctc: str | None = None
    location: str | None = None
    deadline: str | None = None
    eligibility: str | None = None
    attachments: str = "a[href$='.pdf' i], a[href$='.docx' i], a[download]"


class ApplyConfig(BaseModel):
    open: str | None = None  # button that opens the application form
    resume_input: str | None = None  # input[type=file] for the resume PDF
    # selector → profile path, e.g. {"#phone": "phone", "#cgpa": "education.0.cgpa",
    # "#roll": "form_fields.roll_number"}. Missing profile values stop the run (no guessing).
    fields: dict[str, str] = Field(default_factory=dict)
    checkboxes: list[str] = Field(default_factory=list)  # e.g. a declaration checkbox
    submit: str | None = None
    confirmation: str | None = None  # selector/text that proves the submission worked

    @property
    def ready(self) -> bool:
        return bool(self.resume_input and self.submit and self.confirmation)


class SearchConfig(BaseModel):
    ready: str = "body"  # wait for this on a search results page
    job_link: str | None = None  # anchors that are job postings; None → the model picks
    next_page: str | None = None  # "next page" control; None → first page only
    max_pages: int = Field(default=1, ge=1, le=5)


class PortalConfig(BaseModel):
    login: LoginConfig = Field(default_factory=LoginConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    job: JobPageConfig = Field(default_factory=JobPageConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    source: str = "default"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "portal"


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if v is None:  # an empty section (all lines commented out) keeps the defaults
            continue
        out[k] = (
            _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        )
    return out


def load_config(
    portal_name: str, explicit: str | None = None, root: Path | None = None
) -> PortalConfig:
    root = root or CONFIG_DIR
    data: dict[str, Any] = {}
    default = root / "default.yaml"
    if default.exists():
        data = _deep_merge({}, yaml.safe_load(default.read_text()) or {})
    name = explicit or slug(portal_name)
    path = root / f"{name}.yaml"
    source = "default"
    if path.exists():
        data = _deep_merge(data, yaml.safe_load(path.read_text()) or {})
        source = path.name
    return PortalConfig.model_validate({**data, "source": source})
