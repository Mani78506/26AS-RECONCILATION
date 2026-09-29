"""CORS origin configuration for browser clients.

Origins are deployment configuration, never reconciliation or upload logic.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse


LOCAL_FRONTEND_ORIGIN = "http://localhost:3000"


def _configured_origins(value: str | None) -> list[str]:
    """Return unique, valid browser origins from a comma-separated setting."""
    origins: list[str] = []
    for candidate in (value or "").split(","):
        origin = candidate.strip().rstrip("/")
        parsed = urlparse(origin)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path and not parsed.params and not parsed.query and not parsed.fragment:
            if origin not in origins:
                origins.append(origin)
    return origins


def allowed_frontend_origins() -> list[str]:
    """Combine existing origins with the current environment's public UAT origin."""
    origins = _configured_origins(os.environ.get("CORS_ORIGINS"))
    if not origins:
        origins = [LOCAL_FRONTEND_ORIGIN]
    for origin in _configured_origins(os.environ.get("PUBLIC_FRONTEND_ORIGIN")):
        if origin not in origins:
            origins.append(origin)
    return origins


CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
