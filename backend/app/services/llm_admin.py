"""Business logic behind the LLM Settings page: providers, task routing, model lists."""

from __future__ import annotations

import threading
import time

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.llm.catalog import (
    PROVIDERS,
    TASK_DEFAULTS,
    TASK_DESCRIPTIONS,
    TaskParams,
    embedding_model_key,
    spec_for,
)
from app.llm.errors import LLMProviderError
from app.llm.gateway import LLMGateway
from app.llm.reindex import request_reindex
from app.models import Embedding, LLMProvider, LLMTaskConfig
from app.models.enums import AuditActor, LLMTask
from app.schemas.llm import (
    ModelListOut,
    ProviderCreate,
    ProviderOut,
    ProviderSpecOut,
    ProviderUpdate,
    TaskConfigOut,
    TaskConfigUpdate,
    TaskConfigUpdateOut,
)
from app.security.crypto import mask_secret
from app.services.audit import audit


class AdminError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def catalog() -> list[ProviderSpecOut]:
    return [
        ProviderSpecOut(
            kind=s.kind,
            display_name=s.display_name,
            requires_api_key=s.requires_api_key,
            requires_base_url=s.requires_base_url,
            default_base_url=s.default_base_url,
            supports_chat=s.supports_chat,
            supports_embeddings=s.supports_embeddings,
            suggested_models=list(s.suggested_models),
            suggested_embedding_models=list(s.suggested_embedding_models),
            key_hint=s.key_hint,
            notes=s.notes,
        )
        for s in PROVIDERS.values()
    ]


# -- providers ----------------------------------------------------------------------------


def _tasks_using(db: Session, provider_id: int) -> list[LLMTask]:
    rows = db.scalars(
        select(LLMTaskConfig.task).where(
            or_(
                LLMTaskConfig.provider_id == provider_id,
                LLMTaskConfig.fallback_provider_id == provider_id,
            )
        )
    ).all()
    return sorted(set(rows), key=list(LLMTask).index)


def provider_out(db: Session, p: LLMProvider) -> ProviderOut:
    spec = spec_for(p.kind)
    return ProviderOut(
        id=p.id,
        label=p.label,
        kind=p.kind,
        display_name=spec.display_name,
        has_api_key=p.api_key is not None,
        api_key_masked=mask_secret(p.api_key),
        base_url=p.base_url,
        enabled=p.enabled,
        supports_chat=spec.supports_chat,
        supports_embeddings=spec.supports_embeddings,
        last_test_ok=p.last_test_ok,
        last_test_at=p.last_test_at,
        last_test_latency_ms=p.last_test_latency_ms,
        last_test_error=p.last_test_error,
        used_by_tasks=_tasks_using(db, p.id),
    )


def list_providers(db: Session) -> list[ProviderOut]:
    return [provider_out(db, p) for p in db.scalars(select(LLMProvider).order_by(LLMProvider.id))]


def _get_provider(db: Session, provider_id: int) -> LLMProvider:
    p = db.get(LLMProvider, provider_id)
    if p is None:
        raise AdminError(404, f"provider {provider_id} not found")
    return p


def _validate_requirements(p: LLMProvider) -> None:
    spec = spec_for(p.kind)
    if spec.requires_api_key and not p.api_key:
        raise AdminError(422, f"{spec.display_name} requires an API key")
    if spec.requires_base_url and not (p.base_url or spec.default_base_url):
        raise AdminError(422, f"{spec.display_name} requires a base URL")


def create_provider(db: Session, body: ProviderCreate) -> ProviderOut:
    spec = spec_for(body.kind)
    p = LLMProvider(
        label=body.label,
        kind=body.kind,
        api_key=body.api_key,
        base_url=body.base_url or spec.default_base_url,
        enabled=body.enabled,
    )
    _validate_requirements(p)
    db.add(p)
    db.flush()
    audit(
        db,
        AuditActor.USER,
        "llm_provider.created",
        entity_type="llm_provider",
        entity_id=p.id,
        details={"kind": p.kind.value, "label": p.label},
    )
    db.commit()
    _invalidate_models(p.id)
    return provider_out(db, p)


def update_provider(db: Session, provider_id: int, body: ProviderUpdate) -> ProviderOut:
    p = _get_provider(db, provider_id)
    fields = body.model_fields_set
    changed: list[str] = []
    if "label" in fields and body.label is not None:
        p.label = body.label
        changed.append("label")
    if "api_key" in fields and body.api_key:
        p.api_key = body.api_key
        p.last_test_ok = None
        changed.append("api_key")
    if "base_url" in fields:
        p.base_url = body.base_url
        changed.append("base_url")
    if "enabled" in fields and body.enabled is not None:
        p.enabled = body.enabled
        changed.append("enabled")
    _validate_requirements(p)
    audit(
        db,
        AuditActor.USER,
        "llm_provider.updated",
        entity_type="llm_provider",
        entity_id=p.id,
        details={"changed": changed},
    )
    db.commit()
    _invalidate_models(p.id)
    return provider_out(db, p)


def delete_provider(db: Session, provider_id: int) -> None:
    p = _get_provider(db, provider_id)
    using = _tasks_using(db, provider_id)
    if using:
        raise AdminError(
            409,
            "provider is assigned to task(s): "
            + ", ".join(t.value for t in using)
            + ". Reassign them first.",
        )
    db.delete(p)
    audit(
        db,
        AuditActor.USER,
        "llm_provider.deleted",
        entity_type="llm_provider",
        entity_id=provider_id,
    )
    db.commit()
    _invalidate_models(provider_id)


# -- model lists (cached) -----------------------------------------------------------------

_MODEL_TTL_S = 600
_model_cache: dict[tuple[int, bool], tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()


def _invalidate_models(provider_id: int) -> None:
    with _cache_lock:
        for key in [k for k in _model_cache if k[0] == provider_id]:
            del _model_cache[key]


def list_models(
    db: Session, gateway: LLMGateway, provider_id: int, *, embedding: bool
) -> ModelListOut:
    p = _get_provider(db, provider_id)
    spec = spec_for(p.kind)
    suggested = list(spec.suggested_embedding_models if embedding else spec.suggested_models)
    key = (provider_id, embedding)
    with _cache_lock:
        hit = _model_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return ModelListOut(models=hit[1], source="live")
    try:
        models = gateway.backend.list_models(p.kind, p.api_key, p.base_url, embedding=embedding)
    except LLMProviderError as exc:
        return ModelListOut(models=suggested, source="suggested", error=str(exc))
    if not models:
        return ModelListOut(models=suggested, source="suggested")
    with _cache_lock:
        _model_cache[key] = (time.monotonic() + _MODEL_TTL_S, models)
    return ModelListOut(models=models, source="live")


# -- task routing -------------------------------------------------------------------------


def _effective_params(cfg: LLMTaskConfig | None, task: LLMTask) -> TaskParams:
    stored = cfg.params if cfg and cfg.params else {}
    return TaskParams.model_validate({**TASK_DEFAULTS[task].model_dump(), **stored})


def task_config_out(task: LLMTask, cfg: LLMTaskConfig | None) -> TaskConfigOut:
    return TaskConfigOut(
        task=task,
        description=TASK_DESCRIPTIONS[task],
        is_embedding=task is LLMTask.EMBEDDING,
        provider_id=cfg.provider_id if cfg else None,
        model=cfg.model if cfg else None,
        fallback_provider_id=cfg.fallback_provider_id if cfg else None,
        fallback_model=cfg.fallback_model if cfg else None,
        params=_effective_params(cfg, task),
        configured=bool(cfg and cfg.provider_id and cfg.model),
    )


def list_task_configs(db: Session) -> list[TaskConfigOut]:
    rows = {c.task: c for c in db.scalars(select(LLMTaskConfig))}
    return [task_config_out(t, rows.get(t)) for t in LLMTask]


def _check_capability(p: LLMProvider, task: LLMTask, role: str) -> None:
    spec = spec_for(p.kind)
    if task is LLMTask.EMBEDDING and not spec.supports_embeddings:
        raise AdminError(422, f"{role} provider {p.label!r} does not support embeddings")
    if task is not LLMTask.EMBEDDING and not spec.supports_chat:
        raise AdminError(422, f"{role} provider {p.label!r} is embeddings-only")


def update_task_config(db: Session, task: LLMTask, body: TaskConfigUpdate) -> TaskConfigUpdateOut:
    if (body.provider_id is None) != (body.model is None):
        raise AdminError(422, "set both provider and model, or clear both")
    if (body.fallback_provider_id is None) != (body.fallback_model is None):
        raise AdminError(422, "set both fallback provider and fallback model, or neither")
    if task is LLMTask.EMBEDDING and body.fallback_provider_id is not None:
        raise AdminError(
            422, "embeddings cannot have a fallback: a different model's vectors are incompatible"
        )

    primary = _get_provider(db, body.provider_id) if body.provider_id is not None else None
    fallback = (
        _get_provider(db, body.fallback_provider_id)
        if body.fallback_provider_id is not None
        else None
    )
    if primary:
        _check_capability(primary, task, "primary")
    if fallback:
        _check_capability(fallback, task, "fallback")

    cfg = db.get(LLMTaskConfig, task) or LLMTaskConfig(task=task, params={})
    params = dict(cfg.params or {})
    if body.params is not None:
        params.update(body.params.model_dump(exclude_unset=True))
        params = {k: v for k, v in params.items() if v is not None}
    try:
        TaskParams.model_validate({**TASK_DEFAULTS[task].model_dump(), **params})
    except ValueError as exc:
        raise AdminError(422, f"invalid params: {exc}") from None

    reindex = False
    if task is LLMTask.EMBEDDING:
        old_key = (
            embedding_model_key(cfg.provider.kind, cfg.model)
            if cfg.provider is not None and cfg.model
            else None
        )
        new_key = embedding_model_key(primary.kind, body.model) if primary and body.model else None
        has_vectors = db.scalar(select(exists().where(Embedding.id.is_not(None))))
        if new_key != old_key and has_vectors:
            if not body.confirm_reindex:
                raise AdminError(
                    409,
                    "Changing the embedding model re-embeds the whole knowledge base "
                    "(this costs tokens and takes a while). Confirm to continue.",
                )
            if new_key is None:
                raise AdminError(422, "cannot clear the embedding model while vectors exist")
            request_reindex(db, new_key)
            reindex = True

    cfg.provider_id = body.provider_id
    cfg.model = body.model
    cfg.fallback_provider_id = body.fallback_provider_id
    cfg.fallback_model = body.fallback_model
    cfg.params = params
    db.add(cfg)
    audit(
        db,
        AuditActor.USER,
        "llm_task.updated",
        entity_type="llm_task",
        entity_id=task.value,
        details={
            "provider_id": body.provider_id,
            "model": body.model,
            "fallback_provider_id": body.fallback_provider_id,
            "fallback_model": body.fallback_model,
            "params": params,
            "reindex": reindex,
        },
    )
    db.commit()
    db.refresh(cfg)
    return TaskConfigUpdateOut(config=task_config_out(task, cfg), reindex_scheduled=reindex)
