"""Google OAuth2 authentication."""
import os
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from authlib.integrations.starlette_client import OAuth
from db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

oauth = OAuth()


def setup_oauth():
    """Register Google OAuth — call after env vars are loaded."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.warning("Google OAuth not configured (missing GOOGLE_CLIENT_ID/SECRET)")
        return False

    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    logger.info("Google OAuth configured")
    return True


def _is_email_allowed(email: str) -> bool:
    """Check if email is in the whitelist."""
    with get_db() as conn:
        row = conn.execute("SELECT email FROM allowed_emails WHERE email = ?", (email.lower(),)).fetchone()
        return row is not None


def _upsert_user(email: str, name: str, picture: str) -> dict:
    """Create or update user, return user dict."""
    email = email.lower()
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET name = ?, picture_url = ? WHERE email = ?",
                (name, picture, email)
            )
            conn.commit()
            role = existing["role"]
        else:
            conn.execute(
                "INSERT INTO users (email, name, picture_url, role) VALUES (?, ?, ?, 'user')",
                (email, name, picture)
            )
            conn.commit()
            role = "user"
            # Auto-promote admin emails
            admin_emails = ['jose.palhares@zendesk.com', 'josepalhares@gmail.com']
            if email in admin_emails:
                conn.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email,))
                conn.commit()
                role = "admin"

        return {"email": email, "name": name, "picture": picture, "role": role}


def get_current_user(request: Request) -> Optional[dict]:
    """Get the logged-in user from session, or None."""
    return request.session.get("user")


# ─── Login page ──────────────────────────────────────────────

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GoalCast — Sign In</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Inter', system-ui, sans-serif; }</style>
</head>
<body class="bg-white min-h-screen flex items-center justify-center">
    <div class="text-center w-80">
        <div class="w-14 h-14 rounded-2xl bg-[#007AFF] flex items-center justify-center text-white font-bold text-2xl mx-auto mb-6">G</div>
        <h1 class="text-2xl font-bold text-[#1D1D1F] mb-1">GoalCast</h1>
        <p class="text-sm text-[#6E6E73] mb-8">AI Football Predictions</p>
        {message}
        <a href="/auth/login" class="inline-flex items-center gap-3 bg-white border border-[#E5E7EB] rounded-xl px-6 py-3 shadow-sm hover:shadow-md transition text-sm font-medium text-[#1D1D1F]">
            <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
            Sign in with Google
        </a>
        <p class="text-xs text-[#6E6E73] mt-6">Invite-only access</p>
    </div>
</body>
</html>"""

DENIED_PAGE = LOGIN_PAGE.replace("{message}", '<div class="bg-red-50 border border-red-200 text-red-600 rounded-xl px-4 py-3 text-sm mb-6">Access denied — your email is not on the invite list.<br>Contact the admin to get access.</div>')
LOGIN_CLEAN = LOGIN_PAGE.replace("{message}", "")


# ─── Routes ──────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request):
    """Redirect to Google OAuth consent screen."""
    google = oauth.create_client("google")
    if not google:
        return HTMLResponse(LOGIN_CLEAN.replace("Sign in with Google", "OAuth not configured"), status_code=503)

    app_url = os.environ.get("APP_URL", "http://localhost:8000")
    redirect_uri = f"{app_url}/auth/callback"
    return await google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    """Handle Google OAuth callback."""
    google = oauth.create_client("google")
    if not google:
        return RedirectResponse("/auth/login-page")

    try:
        token = await google.authorize_access_token(request)
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        return RedirectResponse("/auth/login-page")

    userinfo = token.get("userinfo")
    if not userinfo:
        return RedirectResponse("/auth/login-page")

    email = userinfo.get("email", "").lower()
    name = userinfo.get("name", email.split("@")[0])
    picture = userinfo.get("picture", "")

    if not _is_email_allowed(email):
        logger.warning(f"Access denied for: {email}")
        return HTMLResponse(DENIED_PAGE)

    user = _upsert_user(email, name, picture)
    request.session["user"] = user
    logger.info(f"User logged in: {email} ({user['role']})")

    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    user = request.session.get("user", {})
    request.session.clear()
    logger.info(f"User logged out: {user.get('email', '?')}")
    return RedirectResponse("/")


@router.get("/me")
async def me(request: Request):
    """Return current user info or null."""
    user = request.session.get("user")
    return {"user": user}


@router.get("/login-page")
async def login_page():
    """Show the login page."""
    return HTMLResponse(LOGIN_CLEAN)
