from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.cli import rotate_all
from app.core.config import get_config
from app.models import LLMProvider, Portal
from app.models.enums import LLMProviderKind
from app.security import crypto


def test_api_key_is_ciphertext_in_db_and_plaintext_in_orm(db: Session) -> None:
    db.add(LLMProvider(label="Claude", kind=LLMProviderKind.ANTHROPIC, api_key="sk-ant-xyz-123456"))
    db.commit()

    raw = db.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one()
    assert "sk-ant" not in raw
    assert raw.startswith("gAAAA")  # Fernet token

    db.expire_all()
    assert db.query(LLMProvider).one().api_key == "sk-ant-xyz-123456"


def test_encrypted_json_column(db: Session) -> None:
    db.add(
        Portal(
            name="Havlock", base_url="https://x", credentials={"username": "u", "password": "p@ss"}
        )
    )
    db.commit()
    raw = db.execute(text("SELECT encrypted_credentials FROM portals")).scalar_one()
    assert "p@ss" not in raw
    db.expire_all()
    assert db.query(Portal).one().credentials == {"username": "u", "password": "p@ss"}


def test_null_secret_stays_null(db: Session) -> None:
    db.add(LLMProvider(label="Ollama", kind=LLMProviderKind.OLLAMA, base_url="http://ollama:11434"))
    db.commit()
    assert db.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one() is None


def test_rotate_all_reencrypts_under_new_key(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add(LLMProvider(label="OpenAI", kind=LLMProviderKind.OPENAI, api_key="sk-openai-abcdef"))
    db.add(Portal(name="P", base_url="https://p", credentials={"password": "pw-123456"}))
    db.commit()

    old = get_config().master_key.get_secret_value()
    new = Fernet.generate_key().decode()
    try:
        monkeypatch.setenv("MASTER_KEY", new)
        monkeypatch.setenv("MASTER_KEY_PREVIOUS", old)
        get_config.cache_clear()
        crypto.reset_cipher_cache()
        assert rotate_all(engine) == 2

        # Retire the old key: data must still be readable.
        monkeypatch.setenv("MASTER_KEY_PREVIOUS", "")
        get_config.cache_clear()
        crypto.reset_cipher_cache()
        fresh = sessionmaker(bind=engine)()
        assert fresh.query(LLMProvider).one().api_key == "sk-openai-abcdef"
        assert fresh.query(Portal).one().credentials == {"password": "pw-123456"}
        fresh.close()
    finally:
        monkeypatch.undo()
        get_config.cache_clear()
        crypto.reset_cipher_cache()
