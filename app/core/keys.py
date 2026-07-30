"""API key generation and hashing. Plaintext keys are never stored."""

import hashlib
import secrets


def generate_key() -> str:
    return "lk_" + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str:
    return key[:8]
