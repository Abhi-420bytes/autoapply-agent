from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from cryptography.fernet import Fernet

# Must be set before any app module reads the config.
os.environ["MASTER_KEY"] = Fernet.generate_key().decode()
os.environ["MASTER_KEY_PREVIOUS"] = ""
os.environ["API_TOKEN"] = "test-api-token-123456"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="autoapply-test-data-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.llm import get_gateway  # noqa: E402
from app.llm.gateway import LLMGateway  # noqa: E402
from app.models import Base  # noqa: E402
from tests.fakes import FakeBackend  # noqa: E402

API_TOKEN = os.environ["API_TOKEN"]


def make_sqlite_engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = make_sqlite_engine(tmp_path / "test.db")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def gateway(engine: Engine, backend: FakeBackend, sleeps: list[float]) -> LLMGateway:
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return LLMGateway(maker, backend, sleep=sleeps.append)


@pytest.fixture
def client(engine: Engine, gateway: LLMGateway) -> Iterator[TestClient]:
    from app.main import create_app

    app = create_app()
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    def _db() -> Iterator[Session]:
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway] = lambda: gateway
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {API_TOKEN}"
        yield c


@pytest.fixture
def llm_ready(db: Session) -> None:
    """A provider with the repo summarizer and embedding tasks configured."""
    from app.models import LLMProvider, LLMTaskConfig
    from app.models.enums import LLMProviderKind, LLMTask

    p = LLMProvider(label="Test", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.REPO_SUMMARIZER, provider_id=p.id, model="summ", params={}))
    db.add(LLMTaskConfig(task=LLMTask.EMBEDDING, provider_id=p.id, model="emb", params={}))
    db.commit()
