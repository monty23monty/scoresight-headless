from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException, Query, Request, WebSocket, status

from scoresight.core.models import SecurityConfig

ADMIN_COOKIE = "scoresight_admin"
CSRF_COOKIE = "scoresight_csrf"


def constant_time_contains(value: str, candidates: list[str]) -> bool:
    return any(hmac.compare_digest(value, candidate) for candidate in candidates)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


@dataclass(slots=True)
class SecurityDependencies:
    get_security: Callable[[], SecurityConfig]

    async def require_admin(
        self,
        authorization: str | None = Header(default=None),
        scoresight_admin: str | None = Cookie(default=None),
    ) -> None:
        security: SecurityConfig = self.get_security()
        token = bearer_token(authorization) or scoresight_admin or ""
        if not hmac.compare_digest(token, security.admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin required")

    async def require_admin_csrf(
        self,
        request: Request,
        authorization: str | None = Header(default=None),
        scoresight_admin: str | None = Cookie(default=None),
        scoresight_csrf: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> None:
        await self.require_admin(authorization, scoresight_admin)
        if bearer_token(authorization):
            return
        if (
            not scoresight_csrf
            or not x_csrf_token
            or not hmac.compare_digest(scoresight_csrf, x_csrf_token)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

    async def require_read(
        self,
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
        scoresight_admin: str | None = Cookie(default=None),
    ) -> None:
        security: SecurityConfig = self.get_security()
        supplied = bearer_token(authorization) or token or scoresight_admin or ""
        if not constant_time_contains(supplied, [security.admin_token, *security.read_tokens]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="read token required"
            )

    def websocket_allowed(self, websocket: WebSocket) -> bool:
        security: SecurityConfig = self.get_security()
        token = websocket.query_params.get("token") or websocket.cookies.get(ADMIN_COOKIE) or ""
        return constant_time_contains(token, [security.admin_token, *security.read_tokens])


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
