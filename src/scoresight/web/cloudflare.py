from __future__ import annotations

import time
from typing import Any

import httpx
import jwt


class AccessAuthenticationError(ValueError):
    pass


class CloudflareAccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        *,
        client: httpx.AsyncClient | None = None,
        cache_seconds: float = 21600.0,
    ) -> None:
        self.team_domain = team_domain.rstrip("/")
        self.audience = audience
        self.cache_seconds = cache_seconds
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._owns_client = client is None
        self._keys: dict[str, Any] = {}
        self._loaded_at = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _refresh(self) -> None:
        response = await self._client.get(f"{self.team_domain}/cdn-cgi/access/certs")
        response.raise_for_status()
        payload = response.json()
        keys = {
            str(item["kid"]): jwt.PyJWK.from_dict(item).key
            for item in payload.get("keys", [])
            if isinstance(item, dict) and item.get("kid")
        }
        if not keys:
            raise AccessAuthenticationError("Cloudflare returned no signing keys")
        self._keys = keys
        self._loaded_at = time.monotonic()

    async def _key(self, token: str, *, force_refresh: bool = False) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AccessAuthenticationError(
                "invalid Cloudflare Access token header"
            ) from exc
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AccessAuthenticationError(
                "unsupported Cloudflare Access signing header"
            )
        kid = str(header["kid"])
        stale = time.monotonic() - self._loaded_at >= self.cache_seconds
        if force_refresh or kid not in self._keys or stale:
            try:
                await self._refresh()
            except Exception as exc:
                if kid not in self._keys:
                    raise AccessAuthenticationError(
                        "unable to refresh Cloudflare Access signing keys"
                    ) from exc
        key = self._keys.get(kid)
        if key is None:
            raise AccessAuthenticationError("unknown Cloudflare Access signing key")
        return key

    async def _decode(self, token: str, key: Any) -> dict[str, Any]:
        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.team_domain,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )

    async def verify(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AccessAuthenticationError("missing Cloudflare Access assertion")
        key = await self._key(token)
        try:
            return await self._decode(token, key)
        except jwt.InvalidSignatureError:
            key = await self._key(token, force_refresh=True)
            try:
                return await self._decode(token, key)
            except jwt.InvalidTokenError as exc:
                raise AccessAuthenticationError(
                    "invalid Cloudflare Access assertion"
                ) from exc
        except jwt.InvalidTokenError as exc:
            raise AccessAuthenticationError(
                "invalid Cloudflare Access assertion"
            ) from exc
