from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command
from app.models import Base
from tests.conftest import make_sqlite_engine

BACKEND = Path(__file__).resolve().parents[1]


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


def test_upgrade_matches_models_and_downgrades(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    url = f"sqlite:///{db_path}"
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, "head")

    engine = make_sqlite_engine(db_path)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations drifted: {diff}"
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())

    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
