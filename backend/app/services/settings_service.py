"""Typed access to the `settings` table.

Non-secret settings are stored one row per field as JSON and validated through the
AppSettings / Profile models on every read and write. Secret settings (e.g. the GitHub
token) use the encrypted column and are only ever returned masked through the API.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting
from app.models.enums import AuditActor
from app.schemas.settings import (
    SECRET_NAMES,
    AppSettings,
    AppSettingsPatch,
    Profile,
    SecretStatus,
)
from app.security.crypto import mask_secret
from app.services.audit import audit

_APP_PREFIX = "app."
_PROFILE_KEY = "profile"
_SECRET_PREFIX = "secret."  # noqa: S105 — key namespace, not a credential


class UnknownSecretError(KeyError):
    pass


def get_app_settings(db: Session) -> AppSettings:
    rows = db.scalars(select(Setting).where(Setting.key.startswith(_APP_PREFIX))).all()
    stored = {
        r.key.removeprefix(_APP_PREFIX): r.value
        for r in rows
        if r.key.removeprefix(_APP_PREFIX) in AppSettings.model_fields
    }
    return AppSettings.model_validate(stored)


def update_app_settings(
    db: Session, patch: AppSettingsPatch, actor: AuditActor = AuditActor.USER
) -> AppSettings:
    current = get_app_settings(db)
    changes = patch.model_dump(exclude_unset=True)
    merged = AppSettings.model_validate({**current.model_dump(), **changes})

    changed: dict[str, dict[str, Any]] = {}
    for field in changes:
        old, new = getattr(current, field), getattr(merged, field)
        if old == new:
            continue
        key = _APP_PREFIX + field
        row = db.get(Setting, key) or Setting(key=key, is_secret=False)
        row.value = new
        db.add(row)
        changed[field] = {"from": old, "to": new}

    if changed:
        audit(db, actor, "settings.updated", entity_type="settings", details=changed)
    db.commit()
    return merged


def get_profile(db: Session) -> Profile:
    row = db.get(Setting, _PROFILE_KEY)
    return Profile.model_validate(row.value) if row and row.value else Profile()


def set_profile(db: Session, profile: Profile, actor: AuditActor = AuditActor.USER) -> Profile:
    row = db.get(Setting, _PROFILE_KEY) or Setting(key=_PROFILE_KEY, is_secret=False)
    row.value = profile.model_dump(mode="json")
    db.add(row)
    audit(db, actor, "profile.updated", entity_type="settings", entity_id=_PROFILE_KEY)
    db.commit()
    return profile


def _check_secret_name(name: str) -> str:
    if name not in SECRET_NAMES:
        raise UnknownSecretError(name)
    return _SECRET_PREFIX + name


def set_secret(db: Session, name: str, value: str, actor: AuditActor = AuditActor.USER) -> None:
    key = _check_secret_name(name)
    row = db.get(Setting, key) or Setting(key=key, is_secret=True)
    row.secret_value = value
    row.value = None
    db.add(row)
    audit(db, actor, "secret.set", entity_type="settings", entity_id=name)
    db.commit()


def get_secret(db: Session, name: str) -> str | None:
    """Plaintext secret for internal use only — never return this from an endpoint."""
    row = db.get(Setting, _check_secret_name(name))
    return row.secret_value if row else None


def delete_secret(db: Session, name: str, actor: AuditActor = AuditActor.USER) -> bool:
    row = db.get(Setting, _check_secret_name(name))
    if row is None:
        return False
    db.delete(row)
    audit(db, actor, "secret.deleted", entity_type="settings", entity_id=name)
    db.commit()
    return True


def list_secrets(db: Session) -> list[SecretStatus]:
    out = []
    for name in SECRET_NAMES:
        value = get_secret(db, name)
        out.append(SecretStatus(name=name, is_set=value is not None, masked=mask_secret(value)))
    return out


def resume_filename(db: Session, ext: str = "pdf") -> str:
    """Your resume's file name (Settings → Resume file name, else your profile name)."""
    import re

    base = get_app_settings(db).resume_file_name.strip() or get_profile(db).full_name.strip()
    base = re.sub(r"\.pdf$", "", base, flags=re.I)
    base = re.sub(r"[^A-Za-z0-9 _.-]", "", base).strip().replace(" ", "_") or "Resume"
    return f"{base[:80]}.{ext}"


def display_name(db: Session) -> str:
    """Your name for email senders and signatures: profile name, else the resume file name
    setting (e.g. "Abhiram"), else the resume's default author."""
    s = get_app_settings(db).resume_file_name.strip()
    s = s[:-4] if s.lower().endswith(".pdf") else s
    return get_profile(db).full_name.strip() or s.replace("_", " ").strip()
