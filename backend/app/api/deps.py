from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_config

_bearer = HTTPBearer(auto_error=False)


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = get_config().api_token.get_secret_value()
    if creds is None or not hmac.compare_digest(creds.credentials.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
