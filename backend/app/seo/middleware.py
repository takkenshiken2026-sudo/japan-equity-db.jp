from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TechSEOMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path

        if path.startswith("/api"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        elif path in ("/robots.txt", "/ads.txt", "/sitemap.xml", "/llms.txt", "/disclaimer"):
            response.headers["Cache-Control"] = "public, max-age=86400"
        elif path == "/" or path.startswith("/companies") or path.startswith("/industries"):
            response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        return response
