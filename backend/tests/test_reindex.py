from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.llm.gateway import LLMGateway
from app.llm.reindex import pending_reindex, request_reindex, run_pending_reindex
from app.models import Embedding, LLMProvider, LLMTaskConfig, Notification
from app.models.enums import LLMProviderKind, LLMTask

OLD = "openai:text-embedding-3-small"
NEW = "openai:text-embedding-3-large"


def test_reindex_replaces_old_vectors(engine: Engine, db: Session, gateway: LLMGateway) -> None:
    p = LLMProvider(label="O", kind=LLMProviderKind.OPENAI, api_key="sk-openai-xxxxxxxxxx")
    db.add(p)
    db.flush()
    db.add(
        LLMTaskConfig(
            task=LLMTask.EMBEDDING, provider_id=p.id, model="text-embedding-3-large", params={}
        )
    )
    for i in range(3):
        db.add(
            Embedding(
                owner_type="bullet",
                owner_id=i,
                content=f"bullet {i}",
                model_name=OLD,
                dimension=2,
                vector=[1.0, 0.0],
            )
        )
    # one chunk already done by an interrupted earlier run → must be skipped
    db.add(
        Embedding(
            owner_type="bullet",
            owner_id=0,
            content="bullet 0",
            model_name=NEW,
            dimension=64,
            vector=[1.0] * 64,
        )
    )
    request_reindex(db, NEW)
    db.commit()

    maker = sessionmaker(bind=engine, expire_on_commit=False)
    assert run_pending_reindex(gateway, maker) == 2

    db.expire_all()
    rows = db.query(Embedding).all()
    assert {r.model_name for r in rows} == {NEW}
    assert sorted(r.owner_id for r in rows) == [0, 1, 2]
    assert all(r.dimension == 64 for r in rows)
    assert pending_reindex(db) is None
    assert db.query(Notification).filter_by(kind="embedding.reindexed").count() == 1

    assert run_pending_reindex(gateway, maker) == 0  # nothing pending
