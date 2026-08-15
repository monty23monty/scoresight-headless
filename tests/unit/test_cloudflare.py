from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from scoresight.web.cloudflare import AccessAuthenticationError, CloudflareAccessVerifier

TEAM_DOMAIN = "https://scoresight.cloudflareaccess.com"
AUDIENCE = "scoresight-audience"


def signing_material(kid: str = "key-1") -> tuple[object, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


def access_token(
    private_key: object,
    kid: str,
    *,
    audience: str = AUDIENCE,
    issuer: str = TEAM_DOMAIN,
    expires_in: int = 300,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "operator-1",
            "email": "operator@example.com",
            "iss": issuer,
            "aud": [audience],
            "iat": now,
            "exp": now + expires_in,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def verifier_for(jwks: dict[str, object]) -> CloudflareAccessVerifier:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [jwks]}))
    return CloudflareAccessVerifier(
        TEAM_DOMAIN,
        AUDIENCE,
        client=httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_cloudflare_verifier_accepts_valid_assertion() -> None:
    private_key, public_jwk = signing_material()
    verifier = verifier_for(public_jwk)
    claims = await verifier.verify(access_token(private_key, "key-1"))
    assert claims["email"] == "operator@example.com"
    await verifier._client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expires_in"),
    [
        ({"audience": "wrong"}, 300),
        ({"issuer": "https://wrong.cloudflareaccess.com"}, 300),
        ({}, -60),
    ],
)
async def test_cloudflare_verifier_rejects_invalid_claims(
    overrides: dict[str, str], expires_in: int
) -> None:
    private_key, public_jwk = signing_material()
    verifier = verifier_for(public_jwk)
    with pytest.raises(AccessAuthenticationError):
        await verifier.verify(
            access_token(private_key, "key-1", expires_in=expires_in, **overrides)
        )
    await verifier._client.aclose()


@pytest.mark.asyncio
async def test_cloudflare_verifier_refreshes_rotated_key() -> None:
    first_private, first_jwk = signing_material("first")
    second_private, second_jwk = signing_material("second")
    active = {"jwk": first_jwk}
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"keys": [active["jwk"]]})
    )
    client = httpx.AsyncClient(transport=transport)
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE, client=client)
    assert (await verifier.verify(access_token(first_private, "first")))["sub"] == "operator-1"
    active["jwk"] = second_jwk
    assert (await verifier.verify(access_token(second_private, "second")))["sub"] == "operator-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_cloudflare_verifier_rejects_missing_assertion() -> None:
    _, public_jwk = signing_material()
    verifier = verifier_for(public_jwk)
    with pytest.raises(AccessAuthenticationError, match="missing"):
        await verifier.verify(None)
    await verifier._client.aclose()


@pytest.mark.asyncio
async def test_cloudflare_verifier_rejects_empty_jwks_and_bad_header() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": []}))
    )
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE, client=client)
    with pytest.raises(AccessAuthenticationError):
        await verifier.verify("not-a-jwt")
    private_key, _ = signing_material("missing")
    with pytest.raises(AccessAuthenticationError, match="refresh"):
        await verifier.verify(access_token(private_key, "missing"))
    await client.aclose()
