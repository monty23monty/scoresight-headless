from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, WebSocket, status

from scoresight.core.deployment import DeploymentSettings
from scoresight.core.models import SecurityConfig
from scoresight.web.cloudflare import (
    AccessAuthenticationError,
    CloudflareAccessVerifier,
)

ADMIN_COOKIE = "scoresight_admin"
CSRF_COOKIE = "scoresight_csrf"


def constant_time_contains(value: str, candidates: list[str]) -> bool:
    return any(hmac.compare_digest(value, candidate) for candidate in candidates)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    subject: str
    email: str | None = None
    claims: dict[str, Any] | None = None


@dataclass(slots=True)
class SecurityDependencies:
    get_security: Callable[[], SecurityConfig]
    deployment: DeploymentSettings
    access_verifier: CloudflareAccessVerifier | None = None

    async def _cloudflare_identity(self, assertion: str | None) -> AuthIdentity:
        if self.access_verifier is None:
            raise HTTPException(
                status_code=503, detail="Cloudflare authentication unavailable"
            )
        try:
            claims = await self.access_verifier.verify(assertion)
        except AccessAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        subject = str(claims.get("sub") or claims.get("email") or "cloudflare-user")
        email = str(claims["email"]) if claims.get("email") else None
        return AuthIdentity(subject=subject, email=email, claims=claims)

    async def require_admin(self, request: Request) -> AuthIdentity:
        if self.deployment.auth_mode == "cloudflare_access":
            identity = await self._cloudflare_identity(
                request.headers.get("Cf-Access-Jwt-Assertion")
            )
            request.state.identity = identity
            return identity
        security = self.get_security()
        token = bearer_token(
            request.headers.get("Authorization")
        ) or request.cookies.get(ADMIN_COOKIE, "")
        if not hmac.compare_digest(token, security.admin_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="admin required"
            )
        identity = AuthIdentity(subject="local-admin")
        request.state.identity = identity
        return identity

    async def require_admin_csrf(self, request: Request) -> AuthIdentity:
        identity = await self.require_admin(request)
        if self.deployment.auth_mode == "token" and bearer_token(
            request.headers.get("Authorization")
        ):
            return identity
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get("X-CSRF-Token")
        if not cookie or not header or not hmac.compare_digest(cookie, header):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token"
            )
        return identity

    async def require_read(self, request: Request) -> AuthIdentity:
        if self.deployment.auth_mode == "cloudflare_access":
            return await self.require_admin(request)
        security = self.get_security()
        supplied = (
            bearer_token(request.headers.get("Authorization"))
            or request.query_params.get("token")
            or request.cookies.get(ADMIN_COOKIE, "")
        )
        if not constant_time_contains(
            supplied, [security.admin_token, *security.read_tokens]
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="read token required"
            )
        identity = AuthIdentity(subject="local-reader")
        request.state.identity = identity
        return identity

    async def websocket_allowed(self, websocket: WebSocket) -> bool:
        allowed_origins = self.deployment.effective_allowed_origins
        origin = websocket.headers.get("origin")
        if allowed_origins and origin not in allowed_origins:
            return False
        if self.deployment.auth_mode == "cloudflare_access":
            try:
                await self._cloudflare_identity(
                    websocket.headers.get("Cf-Access-Jwt-Assertion")
                )
                return True
            except HTTPException:
                return False
        security = self.get_security()
        token = (
            websocket.query_params.get("token")
            or websocket.cookies.get(ADMIN_COOKIE)
            or ""
        )
        return constant_time_contains(
            token, [security.admin_token, *security.read_tokens]
        )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
