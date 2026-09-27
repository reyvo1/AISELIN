from __future__ import annotations

import base64
import hashlib
import os
import secrets
import re
from datetime import datetime, timezone
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key() -> bytes:
    return hashlib.sha256(settings.master_key.encode("utf-8")).digest()


def put_secret(scope: str, name: str, value: str) -> dict:
    if not value:
        raise ValueError("secret cannot be empty")
    nonce = os.urandom(12)
    aad = f"{scope}:{name}".encode()
    ciphertext = AESGCM(_key()).encrypt(nonce, value.encode(), aad)
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT id,version FROM vault_secrets WHERE scope=? AND name=?", (scope, name)).fetchone()
        if row:
            secret_id, version = row["id"], int(row["version"]) + 1
            conn.execute(
                "UPDATE vault_secrets SET nonce=?,ciphertext=?,version=?,updated_at=? WHERE id=?",
                (base64.b64encode(nonce).decode(), base64.b64encode(ciphertext).decode(), version, now, secret_id),
            )
        else:
            secret_id, version = str(uuid4()), 1
            conn.execute(
                "INSERT INTO vault_secrets VALUES(?,?,?,?,?,?,?,?)",
                (secret_id, scope, name, base64.b64encode(nonce).decode(), base64.b64encode(ciphertext).decode(), version, now, now),
            )
    return {"id": secret_id, "scope": scope, "name": name, "version": version, "updated_at": now}


def get_secret(scope: str, name: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT nonce,ciphertext FROM vault_secrets WHERE scope=? AND name=?", (scope, name)).fetchone()
    if not row:
        return None
    nonce = base64.b64decode(row["nonce"])
    ciphertext = base64.b64decode(row["ciphertext"])
    aad = f"{scope}:{name}".encode()
    return AESGCM(_key()).decrypt(nonce, ciphertext, aad).decode()


def rotate_webhook_secret(app_id: str) -> dict:
    secret = secrets.token_urlsafe(40)
    meta = put_secret(f"webhook:{app_id}", "event", secret)
    with connect() as conn:
        conn.execute("UPDATE connector_states SET secret_version=?,updated_at=? WHERE app_id=?", (meta["version"], _now(), app_id))
    return {**meta, "secret": secret}


_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

def put_deployment_secret(organization_id: str, name: str, value: str) -> dict:
    if not _SECRET_NAME_RE.fullmatch(name):
        raise ValueError("invalid deployment secret name")
    return put_secret(f"deployment:{organization_id}", name, value)

def get_deployment_secret(organization_id: str, name: str) -> str | None:
    if not _SECRET_NAME_RE.fullmatch(name):
        return None
    return get_secret(f"deployment:{organization_id}", name)
