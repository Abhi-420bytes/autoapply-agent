from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.llm import get_gateway
from app.llm.gateway import LLMGateway
from app.llm.usage import UsageSummary, usage_summary
from app.models.enums import LLMTask
from app.schemas.llm import (
    ModelListOut,
    ProviderCreate,
    ProviderOut,
    ProviderSpecOut,
    ProviderTestOut,
    ProviderTestRequest,
    ProviderUpdate,
    TaskConfigOut,
    TaskConfigUpdate,
    TaskConfigUpdateOut,
)
from app.services import llm_admin as svc
from app.services.settings_service import get_app_settings

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/catalog", response_model=list[ProviderSpecOut])
def read_catalog() -> list[ProviderSpecOut]:
    return svc.catalog()


@router.get("/providers", response_model=list[ProviderOut])
def read_providers(db: Session = Depends(get_db)) -> list[ProviderOut]:
    return svc.list_providers(db)


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def add_provider(body: ProviderCreate, db: Session = Depends(get_db)) -> ProviderOut:
    return svc.create_provider(db, body)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def edit_provider(
    provider_id: int, body: ProviderUpdate, db: Session = Depends(get_db)
) -> ProviderOut:
    return svc.update_provider(db, provider_id, body)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_provider(provider_id: int, db: Session = Depends(get_db)) -> Response:
    svc.delete_provider(db, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{provider_id}/test", response_model=ProviderTestOut)
async def test_provider(
    provider_id: int,
    body: ProviderTestRequest,
    gateway: LLMGateway = Depends(get_gateway),
) -> ProviderTestOut:
    try:
        r = await run_in_threadpool(gateway.test_provider, provider_id, body.model)
    except LookupError:
        raise HTTPException(404, f"provider {provider_id} not found") from None
    return ProviderTestOut(
        ok=r.ok,
        latency_ms=r.latency_ms,
        model=r.model,
        error=r.error,
        error_kind=r.error_kind,
        reply=r.reply,
    )


@router.get("/providers/{provider_id}/models", response_model=ModelListOut)
def provider_models(
    provider_id: int,
    embedding: bool = False,
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
) -> ModelListOut:
    return svc.list_models(db, gateway, provider_id, embedding=embedding)


@router.get("/tasks", response_model=list[TaskConfigOut])
def read_tasks(db: Session = Depends(get_db)) -> list[TaskConfigOut]:
    return svc.list_task_configs(db)


@router.put("/tasks/{task}", response_model=TaskConfigUpdateOut)
def write_task(
    task: LLMTask, body: TaskConfigUpdate, db: Session = Depends(get_db)
) -> TaskConfigUpdateOut:
    return svc.update_task_config(db, task, body)


@router.get("/usage", response_model=UsageSummary)
def read_usage(db: Session = Depends(get_db)) -> UsageSummary:
    s = get_app_settings(db)
    return usage_summary(db, s.timezone, s.monthly_budget_usd)
