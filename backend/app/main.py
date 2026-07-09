from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

from app.collection_log import collection_log_root, read_collection_manifest
from app.db import Company, Filing, Financial, QuarterlyFinancial, RealEstateProperty, RealEstateSync, SessionLocal, StockQuote, init_db
from app.db_maintenance import checkpoint_sqlite_wal
from app.routers import calendar, companies, screening, themes, trending
from app.seo.helpers import build_home_meta_description, build_website_json_ld
from app.seo.canonical_host import CanonicalHostMiddleware
from app.seo.middleware import TechSEOMiddleware
from app.seo.routes import render_404, router as seo_router

app = FastAPI(title="株チェック API", version="0.1.0")

_app_dir = Path(__file__).resolve().parent
MOCK_DIR = _app_dir.parent / "mock"
if not MOCK_DIR.exists():
    MOCK_DIR = _app_dir.parent.parent / "mock"

app.add_middleware(TechSEOMiddleware)
app.add_middleware(CanonicalHostMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router, prefix="/api")
app.include_router(screening.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(themes.router, prefix="/api")
app.include_router(trending.router, prefix="/api")
app.include_router(seo_router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    checkpoint_sqlite_wal("PASSIVE")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith("/api"):
        return render_404(request)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/api/collection-logs")
def collection_logs(
    source: Optional[str] = None,
    edinet_code: Optional[str] = None,
    limit: int = 50,
) -> dict:
    items = read_collection_manifest(source=source, edinet_code=edinet_code, limit=min(limit, 200))
    return {
        "count": len(items),
        "root": str(collection_log_root()),
        "items": items,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stats")
def stats() -> dict:
    db = SessionLocal()
    try:
        base = {
            "companies": db.scalar(select(func.count()).select_from(Company)) or 0,
            "filings": db.scalar(select(func.count()).select_from(Filing)) or 0,
            "financials": db.scalar(select(func.count()).select_from(Financial)) or 0,
            "listed": db.scalar(
                select(func.count()).select_from(Company).where(Company.listing_status == "上場")
            )
            or 0,
            "stock_quotes": db.scalar(select(func.count()).select_from(StockQuote)) or 0,
            "real_estate_properties": db.scalar(
                select(func.count()).select_from(RealEstateProperty)
            )
            or 0,
            "real_estate_synced": db.scalar(select(func.count()).select_from(RealEstateSync)) or 0,
            "quarterly_financials": db.scalar(select(func.count()).select_from(QuarterlyFinancial)) or 0,
        }
        return base
    finally:
        db.close()


@app.get("/assets/charts.js", response_model=None)
def charts_js():
    path = MOCK_DIR / "charts.js"
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "public, max-age=3600"})


def _site_url(request: Request) -> str:
    if settings.site_url:
        return settings.site_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def _listed_count() -> int:
    db = SessionLocal()
    try:
        return (
            db.scalar(
                select(func.count()).select_from(Company).where(Company.listing_status == "上場")
            )
            or 0
        )
    finally:
        db.close()


def _render_mock_html(filename: str, request: Request) -> HTMLResponse:
    path = MOCK_DIR / filename
    text = path.read_text(encoding="utf-8")
    site = _site_url(request)
    text = text.replace("__SITE_URL__", site)
    if "__META_DESCRIPTION__" in text:
        text = text.replace("__META_DESCRIPTION__", build_home_meta_description(_listed_count()))
    if "__GOOGLE_VERIFICATION__" in text:
        tag = (
            f'<meta name="google-site-verification" content="{settings.google_site_verification}" />'
            if settings.google_site_verification
            else ""
        )
        text = text.replace("__GOOGLE_VERIFICATION__", tag)
    if "__SITE_JSON_LD__" in text:
        ld = json.dumps(build_website_json_ld(site), ensure_ascii=False)
        text = text.replace("__SITE_JSON_LD__", ld)
    return HTMLResponse(text)


@app.get("/", response_model=None)
def index(request: Request):
    return _render_mock_html("index.html", request)


@app.get("/disclaimer", response_model=None)
def disclaimer_page(request: Request):
    return _render_mock_html("disclaimer.html", request)
