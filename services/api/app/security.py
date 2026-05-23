from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def hash_password(password: str, salt: str | None = None) -> str:
    actual_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt.encode("utf-8"), 390000)
    return f"{actual_salt}${digest.hex()}"


def verify_password(password: str, stored_value: str) -> bool:
    salt, expected_hash = stored_value.split("$", 1)
    candidate = hash_password(password, salt)
    return secrets.compare_digest(candidate, stored_value)


def mint_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def future_utc(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)
