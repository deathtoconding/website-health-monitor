"""Input normalization and validation for monitored website URLs."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


class UrlValidationError(ValueError):
    """A URL is not a supported, unambiguous HTTP(S) target."""


def normalize_website_url(raw: str) -> str:
    """Return a canonical HTTP(S) URL suitable for persistence and duplicate checks.

    Fragments are removed because they are not sent to the origin. Default ports,
    host casing, and an absent path are normalized. Credentials and whitespace
    are rejected so they cannot leak into logs or make URL interpretation unclear.
    """
    if not isinstance(raw, str):
        raise UrlValidationError("URL must be a string")
    candidate = raw.strip()
    if not candidate:
        raise UrlValidationError("URL must not be empty")
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise UrlValidationError("URL must not contain whitespace or control characters")
    try:
        parts = urlsplit(candidate)
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise UrlValidationError("URL is malformed") from exc

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UrlValidationError("Only HTTP and HTTPS URLs are supported")
    if not hostname:
        raise UrlValidationError("URL must include a hostname")
    if parts.username is not None or parts.password is not None:
        raise UrlValidationError("URL credentials are not allowed; use a public endpoint")
    if port is not None and not 1 <= port <= 65535:
        raise UrlValidationError("URL port must be between 1 and 65535")

    try:
        try:
            address = ipaddress.ip_address(hostname)
            ascii_host = address.compressed.lower()
        except ValueError:
            ascii_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise UrlValidationError("URL hostname is invalid") from exc
    if not ascii_host:
        raise UrlValidationError("URL must include a valid hostname")

    authority_host = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    default_port = 80 if scheme == "http" else 443
    authority = authority_host
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, authority, path, parts.query, ""))
