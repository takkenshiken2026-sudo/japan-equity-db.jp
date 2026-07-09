from __future__ import annotations

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import settings


class CanonicalHostMiddleware(BaseHTTPMiddleware):
    """本番 SITE_URL と異なるホストへのアクセスを正規URLへ301リダイレクト。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        site = (settings.site_url or "").strip()
        if site and not request.url.path.startswith("/api"):
            canonical = urlparse(site)
            if canonical.netloc and request.url.netloc and request.url.netloc != canonical.netloc:
                target = f"{canonical.scheme}://{canonical.netloc}{request.url.path}"
                if request.url.query:
                    target = f"{target}?{request.url.query}"
                return RedirectResponse(url=target, status_code=301)
        return await call_next(request)
