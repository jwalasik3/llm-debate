"""Shared browser login, applied before routing (including static files and SSE)."""

import base64
import binascii
import os
import secrets

from starlette.responses import JSONResponse


class SharedLogin:
    def __init__(self, app, username: str, password: str):
        self.app = app
        self.username = username.encode("utf-8")
        self.password = password.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # A separate, deliberately minimal endpoint for infrastructure probes.
        if scope["path"] == "/healthz" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        username, password = b"", b""
        try:
            scheme, token = headers.get(b"authorization", b"").split(None, 1)
            if scheme.lower() == b"basic":
                username, password = base64.b64decode(token, validate=True).split(b":", 1)
        except (ValueError, binascii.Error):
            pass
        valid_user = secrets.compare_digest(username, self.username)
        valid_password = secrets.compare_digest(password, self.password)
        if not (valid_user and valid_password):
            response = JSONResponse(
                {"detail": "Wymagane logowanie."},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="llm-debate", charset="UTF-8"',
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
            return
        # Basic credentials are sent automatically by browsers. Reject cross-origin
        # writes so another website cannot spend the shared account's API quota.
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            host = headers.get(b"host", b"")
            allowed = {b"https://" + host, b"http://" + host}
            if origin is not None and origin not in allowed:
                await JSONResponse({"detail": "Niedozwolone źródło."}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def configure_login(app):
    username = os.getenv("APP_USERNAME", "")
    password = os.getenv("APP_PASSWORD", "")
    required = os.getenv("AUTH_REQUIRED", "false").lower()
    if required not in {"true", "false"}:
        raise ValueError("AUTH_REQUIRED must be true or false")
    if required == "true" or username or password:
        if not username or not password or ":" in username:
            raise ValueError("Set APP_USERNAME (without ':') and APP_PASSWORD to enable login")
        app.add_middleware(SharedLogin, username=username, password=password)
