"""Runtime configuration.

Values come from environment variables and, when present, from a ``.env`` file
at the project root.  Nothing is required for the viewer itself: accounts and
the local assistant work without any external service.  Only the language model
needs an API key.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_PATH = os.path.join(ROOT, ".env")
INSTANCE_DIR = os.path.join(ROOT, "instance")

_loaded = False


def load_env(path: str | None = None) -> None:
    """Read a simple ``KEY=VALUE`` file without extra dependencies."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    target = path or ENV_PATH
    if not os.path.isfile(target):
        return
    try:
        with open(target, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def secret_key() -> str:
    """Flask session secret, generated once and kept in ``instance/``."""
    load_env()
    configured = os.environ.get("SPHEREX_SECRET_KEY", "").strip()
    if configured:
        return configured
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "secret_key")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
        except OSError:
            pass
    value = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def ai_settings() -> dict:
    """Configuration for the OpenAI-compatible assistant."""
    load_env()
    api_key = (
        os.environ.get("SPHEREX_AI_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    base_url = (
        os.environ.get("SPHEREX_AI_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = os.environ.get("SPHEREX_AI_MODEL", "").strip() or "gpt-4o-mini"
    try:
        max_tokens = int(os.environ.get("SPHEREX_AI_MAX_TOKENS", "1200"))
    except ValueError:
        max_tokens = 1200
    host = urlparse(base_url).netloc or base_url
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "max_tokens": max(200, min(max_tokens, 4000)),
        "provider": host,
        "configured": bool(api_key),
    }


def public_ai_status() -> dict:
    settings = ai_settings()
    return {
        "configured": settings["configured"],
        "model": settings["model"] if settings["configured"] else None,
        "provider": settings["provider"] if settings["configured"] else None,
        "mode": "llm" if settings["configured"] else "local",
        "base_url": settings["base_url"] if settings["configured"] else None,
    }


# Load ``.env`` as soon as the package is imported so that modules which read
# environment variables at import time (for example ``db.DB_PATH``) see it.
load_env()
