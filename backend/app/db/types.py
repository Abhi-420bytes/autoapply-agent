"""Custom column types.

- EncryptedText / EncryptedJSON: transparently Fernet-encrypt on write and decrypt on
  read, so credentials are never stored in plaintext and no call site can forget to.
- VectorType: pgvector `vector` on PostgreSQL, JSON elsewhere (SQLite in unit tests).
  Dimension is intentionally unconstrained at the column level because the embedding
  model is user-configurable; every row stores its own model name + dimension.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.security import crypto
from app.security.redaction import register_secret


class EncryptedText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else crypto.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        plaintext = crypto.decrypt(value)
        register_secret(plaintext)
        return plaintext


class EncryptedJSON(TypeDecorator[Any]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else crypto.encrypt_json(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        data = crypto.decrypt_json(value)
        _register_leaves(data)
        return data


def _register_leaves(data: Any) -> None:
    if isinstance(data, dict):
        for v in data.values():
            _register_leaves(v)
    elif isinstance(data, list):
        for v in data:
            _register_leaves(v)
    elif isinstance(data, str):
        register_secret(data)


class VectorType(TypeDecorator[list[float]]):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        return [float(x) for x in value]
