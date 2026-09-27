from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from app.core.config import settings

_CACHE: dict[str, Any] = {"expires": 0.0, "keys": {}}
_LOCK = asyncio.Lock()


class OidcError(RuntimeError):
    pass


async def _jwks() -> dict[str, Any]:
    now=time.time()
    if _CACHE["expires"] > now and _CACHE["keys"]:
        return _CACHE["keys"]
    async with _LOCK:
        now=time.time()
        if _CACHE["expires"] > now and _CACHE["keys"]:
            return _CACHE["keys"]
        url=settings.oidc_jwks_url or (settings.oidc_issuer.rstrip('/')+'/.well-known/jwks.json' if settings.oidc_issuer else '')
        if not url: raise OidcError('OIDC JWKS URL is not configured')
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                r=await client.get(url); r.raise_for_status(); data=r.json()
        except Exception as exc: raise OidcError(f'failed to fetch OIDC JWKS: {exc}') from exc
        keys={k.get('kid'):k for k in data.get('keys',[]) if k.get('kid')}
        if not keys: raise OidcError('OIDC JWKS contains no keyed public keys')
        _CACHE['keys']=keys; _CACHE['expires']=now+max(30,settings.oidc_jwks_cache_seconds)
        return keys


async def validate_oidc_token(token: str) -> dict[str, Any]:
    if not settings.oidc_enabled: raise OidcError('OIDC is disabled')
    try: header=jwt.get_unverified_header(token)
    except Exception as exc: raise OidcError('invalid OIDC JWT header') from exc
    kid=header.get('kid'); keys=await _jwks(); jwk=keys.get(kid)
    if not jwk: raise OidcError('OIDC signing key not found')
    key=PyJWK.from_dict(jwk).key
    options={"require":["exp","iat","sub"]}
    kwargs: dict[str,Any]={"algorithms":[header.get('alg','RS256')],"issuer":settings.oidc_issuer or None,"options":options}
    if settings.oidc_audience: kwargs['audience']=settings.oidc_audience
    else: kwargs['options']={**options,"verify_aud":False}
    try: claims=jwt.decode(token,key=key,**kwargs)
    except Exception as exc: raise OidcError(f'OIDC token validation failed: {exc}') from exc
    return claims
