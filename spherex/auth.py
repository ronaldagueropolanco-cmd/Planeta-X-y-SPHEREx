"""Session authentication, validation and small abuse guards.

Passwords are hashed with Werkzeug (PBKDF2/scrypt) and sessions are signed
cookies.  State-changing authenticated endpoints also require the CSRF token
that is issued with the session.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from functools import wraps

from flask import g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

SESSION_USER = "spherex_user"
SESSION_CSRF = "spherex_csrf"

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 600

# A real hash used to spend comparable time when the account does not exist.
_DUMMY_HASH = generate_password_hash("spherex-dummy-password")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def current_user() -> dict | None:
    user_id = session.get(SESSION_USER)
    if not user_id:
        return None
    user = db.get_user(int(user_id))
    if user is None:
        session.clear()
    return user


def login_session(user: dict) -> str:
    session.clear()
    session.permanent = True
    session[SESSION_USER] = int(user["id"])
    token = secrets.token_urlsafe(24)
    session[SESSION_CSRF] = token
    db.touch_login(int(user["id"]))
    return token


def logout_session() -> None:
    session.clear()


def csrf_token() -> str | None:
    if not session.get(SESSION_USER):
        return None
    token = session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(24)
        session[SESSION_CSRF] = token
    return token


def require_csrf() -> None:
    expected = session.get(SESSION_CSRF)
    provided = request.headers.get("X-CSRF-Token", "")
    if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
        raise AuthError("Token de seguridad inválido. Recarga la página.", 403)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            raise AuthError("Inicia sesión para usar esta función.", 401)
        g.user = user
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user.get("display_name") or user["username"],
        "role": user.get("role", "user"),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
        "stats": db.user_stats(int(user["id"])),
    }


def validate_registration(payload: dict) -> dict:
    username = str(payload.get("username", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    display_name = str(payload.get("display_name", "")).strip() or None

    if not USERNAME_RE.match(username):
        raise AuthError(
            "El usuario debe tener entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo."
        )
    if not EMAIL_RE.match(email):
        raise AuthError("Introduce un correo electrónico válido.")
    if len(password) < 8:
        raise AuthError("La contraseña debe tener al menos 8 caracteres.")
    if len(password) > 128:
        raise AuthError("La contraseña es demasiado larga.")
    if display_name and len(display_name) > 60:
        raise AuthError("El nombre visible no puede superar 60 caracteres.")
    return {
        "username": username,
        "email": email,
        "password": password,
        "display_name": display_name,
    }


def create_account(payload: dict) -> dict:
    data = validate_registration(payload)
    try:
        return db.create_user(
            data["username"],
            data["email"],
            generate_password_hash(data["password"]),
            data["display_name"],
        )
    except Exception as exc:
        message = str(exc).lower()
        if "users.username" in message:
            raise AuthError("Ese nombre de usuario ya está registrado.", 409) from exc
        if "users.email" in message:
            raise AuthError("Ese correo ya está registrado.", 409) from exc
        raise


def authenticate(login: str, password: str) -> dict | None:
    login = login.strip()
    if not login or not password:
        return None
    user = db.get_user_by_login(login)
    if user is None:
        # Constant-ish work even when the account does not exist.
        check_password_hash(_DUMMY_HASH, password)
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "local"


def throttle_key(login: str = "") -> str:
    return f"{_client_ip()}:{login.strip().lower()}"


def check_throttle(key: str) -> None:
    now = time.time()
    with _attempts_lock:
        history = [t for t in _attempts.get(key, []) if now - t < WINDOW_SECONDS]
        _attempts[key] = history
        if len(history) >= MAX_ATTEMPTS:
            raise AuthError(
                "Demasiados intentos fallidos. Espera unos minutos antes de volver a probar.",
                429,
            )


def record_failure(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)
