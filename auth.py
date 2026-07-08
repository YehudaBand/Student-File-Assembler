"""Optional Google Workspace login for the Flask UI.

Enable with GOOGLE_AUTH_ENABLED=1. Restricts access to users whose Google
account belongs to GOOGLE_ALLOWED_DOMAIN (Workspace hosted domain).

This is separate from Drive API credentials in drive.py — see DEPLOY_VERCEL.md
for how app login and Drive access fit together on serverless.
"""

from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, abort, redirect, request, session, url_for
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from werkzeug.middleware.proxy_fix import ProxyFix

auth_bp = Blueprint("auth", __name__)

# OpenID scopes only — Drive scopes stay in drive.py / service credentials.
LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def auth_enabled() -> bool:
    return os.getenv("GOOGLE_AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def allowed_domain() -> str:
    return os.getenv("GOOGLE_ALLOWED_DOMAIN", "masterschool.com").strip().lower()


def _oauth_client_config() -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required when auth is enabled")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def _redirect_uri() -> str:
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("APP_BASE_URL must be set when Google auth is enabled (e.g. https://hcm2-assembler.vercel.app)")
    return f"{base}/auth/callback"


def _flow() -> Flow:
    return Flow.from_client_config(
        _oauth_client_config(),
        scopes=LOGIN_SCOPES,
        redirect_uri=_redirect_uri(),
    )


def _verify_domain(email: str, hosted_domain: str | None) -> None:
    domain = allowed_domain()
    if hosted_domain and hosted_domain.lower() == domain:
        return
    if email and email.lower().endswith(f"@{domain}"):
        return
    abort(403, description=f"Sign-in restricted to @{domain} Google accounts")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return view(*args, **kwargs)
        if not session.get("user_email"):
            nxt = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=nxt))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.get("/auth/login")
def login():
    if not auth_enabled():
        abort(404)
    flow = _flow()
    auth_url, _state = flow.authorization_url(
        access_type="online",
        include_granted_scopes="true",
        prompt="select_account",
        hd=allowed_domain(),
    )
    session["oauth_state"] = _state
    session["auth_next"] = request.args.get("next") or "/"
    return redirect(auth_url)


@auth_bp.get("/auth/callback")
def callback():
    if not auth_enabled():
        abort(404)

    state = session.pop("oauth_state", None)
    if state is None:
        abort(400, description="Missing OAuth state — start login again")

    flow = _flow()
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    if not creds or not creds.id_token:
        abort(401, description="Google did not return an ID token")

    info = id_token.verify_oauth2_token(
        creds.id_token,
        GoogleRequest(),
        os.getenv("GOOGLE_CLIENT_ID"),
    )

    email = info.get("email", "")
    hosted_domain = info.get("hd")
    if not info.get("email_verified"):
        abort(403, description="Google account email is not verified")
    _verify_domain(email, hosted_domain)

    session["user_email"] = email
    session["user_name"] = info.get("name", "")
    session["user_picture"] = info.get("picture", "")

    nxt = session.pop("auth_next", "/")
    if not nxt.startswith("/"):
        nxt = "/"
    return redirect(nxt)


@auth_bp.get("/auth/logout")
def logout():
    session.clear()
    return redirect("/")


@auth_bp.get("/auth/me")
def me():
    if not auth_enabled():
        return {"auth_enabled": False}
    if not session.get("user_email"):
        return {"authenticated": False}, 401
    return {
        "authenticated": True,
        "email": session["user_email"],
        "name": session.get("user_name", ""),
        "picture": session.get("user_picture", ""),
        "allowed_domain": allowed_domain(),
    }


def register_auth(app) -> None:
    """Wire auth blueprint + global before_request guard when enabled."""
    if os.getenv("VERCEL"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.register_blueprint(auth_bp)

    if not auth_enabled():
        return

    secret = os.getenv("FLASK_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("FLASK_SECRET_KEY is required when GOOGLE_AUTH_ENABLED=1")
    app.secret_key = secret

    public_paths = {"/auth/login", "/auth/callback", "/auth/logout"}

    @app.before_request
    def require_workspace_login():
        if request.path.startswith("/static/"):
            return None
        if request.path in public_paths:
            return None
        if request.path == "/auth/me":
            return None
        if session.get("user_email"):
            return None
        if request.path.startswith("/api/"):
            return {"error": "Authentication required", "login": "/auth/login"}, 401
        return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))
