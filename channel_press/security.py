from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .timeutil import utcnow

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except ValueError:
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(40)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_valid(expected: str | None, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def session_expiry(hours: int):
    return utcnow() + timedelta(hours=hours)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact(text: str, secrets_to_redact: list[str] | None = None) -> str:
    """Remove obvious credentials from diagnostics and error messages."""
    out = text
    for value in secrets_to_redact or []:
        if value:
            out = out.replace(value, "[redacted]")
    import re

    patterns = [
        r"(?i)(api[_-]?hash|api[_-]?id|token|password|session)\s*[:=]\s*\S+",
        r"\b[0-9a-f]{32,}\b",
    ]
    for pattern in patterns:
        out = re.sub(pattern, "[redacted]", out)
    return out


def validate_public_http_url(url: str, allowed_hosts: set[str] | None = None) -> tuple[bool, str]:
    """Validate an external URL and reject local/private SSRF targets.

    DNS is resolved to block hostnames that point at loopback, link-local or
    private ranges. Redirects still need re-validation by the fetcher.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "Malformed URL."
    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https URLs are allowed."
    if not parsed.hostname:
        return False, "URL has no hostname."
    if parsed.username or parsed.password:
        return False, "Credentials in URLs are not allowed."
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        return False, "Localhost URLs are blocked."
    if allowed_hosts is not None and host not in allowed_hosts:
        return False, "Host is not in the allowlist."
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError:
            return False, "Host could not be resolved."
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    if not addresses:
        return False, "Host has no usable address."
    for address in addresses:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return False, "Private, local or reserved network targets are blocked."
    return True, "ok"


def safe_filename(name: str) -> str:
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- "
    cleaned = "".join(ch for ch in name if ch in keep).strip().replace(" ", "_")
    return cleaned[:120] or "upload"
