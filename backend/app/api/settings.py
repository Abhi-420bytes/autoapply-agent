from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.settings import AppSettings, AppSettingsPatch, Profile, SecretStatus, SecretUpdate
from app.services import settings_service as svc
from app.services.settings_service import UnknownSecretError

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def read_settings(db: Session = Depends(get_db)) -> AppSettings:
    return svc.get_app_settings(db)


@router.patch("", response_model=AppSettings)
def patch_settings(patch: AppSettingsPatch, db: Session = Depends(get_db)) -> AppSettings:
    try:
        return svc.update_app_settings(db, patch)
    except ValidationError as exc:
        # The patch model only checks types; range/format rules run on the merged model.
        raise RequestValidationError(exc.errors(include_url=False)) from None


@router.get("/profile", response_model=Profile)
def read_profile(db: Session = Depends(get_db)) -> Profile:
    return svc.get_profile(db)


@router.put("/profile", response_model=Profile)
def write_profile(profile: Profile, db: Session = Depends(get_db)) -> Profile:
    return svc.set_profile(db, profile)


class SkillIn(BaseModel):
    skill: str = Field(min_length=1, max_length=80)


@router.post("/profile/skills", response_model=Profile)
def add_skill(body: SkillIn, db: Session = Depends(get_db)) -> Profile:
    """You confirmed you have this skill (e.g. clicked a missing skill in a score report).
    It becomes a fact the resume writer may use; nothing else in the profile changes."""
    profile = svc.get_profile(db)
    skill = " ".join(body.skill.split())
    if skill.lower() not in {s.lower() for s in profile.skills}:
        profile.skills = [*profile.skills, skill]
        profile = svc.set_profile(db, profile)
    return profile


@router.get("/secrets", response_model=list[SecretStatus])
def read_secrets(db: Session = Depends(get_db)) -> list[SecretStatus]:
    return svc.list_secrets(db)


def _known(name: str) -> None:
    try:
        svc._check_secret_name(name)
    except UnknownSecretError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown secret {name!r}") from None


@router.put("/secrets/{name}", response_model=SecretStatus)
def write_secret(name: str, body: SecretUpdate, db: Session = Depends(get_db)) -> SecretStatus:
    _known(name)
    svc.set_secret(db, name, body.value)
    return next(s for s in svc.list_secrets(db) if s.name == name)


@router.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
def remove_secret(name: str, db: Session = Depends(get_db)) -> Response:
    _known(name)
    if not svc.delete_secret(db, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"secret {name!r} is not set")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
