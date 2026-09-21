"""Dashboard login page + signed cookie sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from secrets import compare_digest, token_hex
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette import status as http_status

from bot.core.config import Settings

COOKIE_NAME = "moreney_dash"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

PUBLIC_DASHBOARD_PATHS = frozenset(
    {
        "/health",
        "/login",
        "/logout",
        "/integrations/alphai/webhook",
        "/favicon.ico",
    }
)

HTML_DASHBOARD_PATHS = frozenset(
    {
        "/",
        "/fleet",
        "/dashboard",
        "/live/dashboard",
        "/live/dashboard/legacy",
        "/live/momentum",
        "/live/momentum/volatile",
        "/live/momentum/short-weakest",
        "/live/micro/dashboard",
        "/paper/dashboard",
        "/paper/dashboard-lite",
        "/strategy-lab",
        "/lab",
        "/login",
    }
)


def _secret(settings: Settings) -> str:
    configured = settings.dashboard_session_secret
    if configured and configured.get_secret_value():
        return configured.get_secret_value()
    password = settings.dashboard_basic_auth_password
    pwd = password.get_secret_value() if password else "unset"
    # Stable fallback derived from dashboard credentials (not ideal, but works offline).
    return hashlib.sha256(
        f"{settings.dashboard_basic_auth_username}:{pwd}:moreney-dashboard".encode()
    ).hexdigest()


def issue_session_token(settings: Settings, username: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    nonce = token_hex(8)
    payload = f"{username}:{expires}:{nonce}"
    sig = hmac.new(
        _secret(settings).encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(settings: Settings, token: str | None) -> str | None:
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    username, expires_raw, nonce, sig = parts
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    payload = f"{username}:{expires_raw}:{nonce}"
    expected = hmac.new(
        _secret(settings).encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not compare_digest(expected, sig):
        return None
    if not compare_digest(username, settings.dashboard_basic_auth_username):
        return None
    return username


def credentials_valid(settings: Settings, username: str, password: str) -> bool:
    configured_password = settings.dashboard_basic_auth_password
    if configured_password is None or not configured_password.get_secret_value():
        return False
    user_ok = compare_digest(username, settings.dashboard_basic_auth_username)
    pass_ok = compare_digest(password, configured_password.get_secret_value())
    return bool(user_ok and pass_ok)


def parse_basic_auth(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization") or ""
    if header[:6].lower() != "basic ":
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def request_is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return forwarded.lower() == "https"


def set_session_cookie(
    response: Response,
    settings: Settings,
    username: str,
    *,
    secure: bool = False,
) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=issue_session_token(settings, username),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool = False) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/", secure=secure)


def request_has_valid_session(request: Request, settings: Settings) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return verify_session_token(settings, token) is not None


def request_has_dashboard_access(request: Request, settings: Settings) -> bool:
    if request_has_valid_session(request, settings):
        return True
    basic = parse_basic_auth(request)
    return bool(basic) and credentials_valid(settings, basic[0], basic[1])


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or request.url.path in HTML_DASHBOARD_PATHS


def dashboard_auth_gate(request: Request, settings: Settings) -> Response | None:
    """Block unauthenticated dashboard/API traffic when login is enabled.

    Returns a response to send immediately, or None to continue.
    """
    if not settings.dashboard_basic_auth_enabled:
        return None
    if request.url.path in PUBLIC_DASHBOARD_PATHS:
        return None
    configured_password = settings.dashboard_basic_auth_password
    if configured_password is None or not configured_password.get_secret_value():
        return JSONResponse(
            {"detail": "Dashboard auth enabled but password is not configured"},
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if request_has_dashboard_access(request, settings):
        return None
    if wants_html(request):
        next_path = request.url.path or "/live/momentum"
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return login_redirect(next_path)
    return JSONResponse(
        {"detail": "Unauthorized dashboard access"},
        status_code=http_status.HTTP_401_UNAUTHORIZED,
    )


def login_redirect(next_path: str = "/live/momentum") -> RedirectResponse:
    safe = next_path if next_path.startswith("/") else "/live/momentum"
    return RedirectResponse(url=f"/login?next={quote(safe)}", status_code=303)


def render_login_page(
    *,
    next_path: str = "/live/momentum",
    error: str | None = None,
) -> HTMLResponse:
    err_html = (
        f'<p class="error">{_esc(error)}</p>' if error else ""
    )
    safe_next = next_path if next_path.startswith("/") else "/live/momentum"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Moreney Login</title>
  <style>
    :root {{
      --bg:#0f1419; --panel:#1a222d; --text:#e7ecf3; --muted:#8b9bb4;
      --accent:#3d9cf0; --bad:#f07178; --line:#2a3544;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; min-height:100vh; display:grid; place-items:center;
      font-family:"IBM Plex Sans","Segoe UI",sans-serif; color:var(--text);
      background:
        radial-gradient(900px 500px at 15% -10%, #1b3a57 0%, transparent 55%),
        radial-gradient(700px 420px at 100% 0%, #243447 0%, transparent 50%),
        var(--bg);
      padding:1.25rem;
    }}
    .card {{
      width:min(420px,100%);
      background:color-mix(in srgb, var(--panel) 92%, transparent);
      border:1px solid var(--line);
      padding:1.5rem 1.4rem 1.35rem;
    }}
    h1 {{
      margin:0;
      font-family:"IBM Plex Serif",Georgia,serif;
      font-size:2rem;
      letter-spacing:-0.03em;
    }}
    .sub {{ color:var(--muted); margin:.4rem 0 1.2rem; font-size:.92rem; }}
    label {{ display:block; font-size:.78rem; color:var(--muted); margin:0 0 .35rem; text-transform:uppercase; letter-spacing:.06em; }}
    input {{
      width:100%; margin:0 0 .9rem; padding:.7rem .75rem;
      border:1px solid var(--line); background:#121820; color:var(--text);
      font:inherit;
    }}
    input:focus {{ outline:1px solid var(--accent); }}
    button {{
      width:100%; padding:.75rem 1rem; border:1px solid var(--accent);
      background:transparent; color:var(--text); font:inherit; cursor:pointer;
      letter-spacing:.04em;
    }}
    button:hover {{ background:color-mix(in srgb, var(--accent) 18%, transparent); }}
    .error {{ color:var(--bad); margin:0 0 .9rem; font-size:.9rem; }}
    .foot {{ margin:1rem 0 0; color:var(--muted); font-size:.75rem; text-align:center; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Moreney</h1>
    <p class="sub">Sign in to open the desk dashboard.</p>
    {err_html}
    <input type="hidden" name="next" value="{_esc(safe_next)}"/>
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" required autofocus/>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required/>
    <button type="submit">Sign in</button>
    <p class="foot">HTTPS only · no withdrawals</p>
  </form>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200 if error is None else 401)


def _esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
