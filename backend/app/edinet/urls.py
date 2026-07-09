from __future__ import annotations

from app.config import settings

DISCLOSURE_VIEWER_BASE = "https://disclosure.edinet-fsa.go.jp/E01EW/BLMainController.jsp"


def edinet_viewer_url(doc_id: str) -> str:
    return (
        f"{DISCLOSURE_VIEWER_BASE}?docID={doc_id}&process=0&force=0"
    )


def edinet_download_url(doc_id: str, kind: str = "pdf") -> str:
    type_map = {"pdf": "1", "xbrl": "2", "csv": "5"}
    doc_type = type_map.get(kind, "1")
    base = settings.edinet_base_url.rstrip("/")
    return f"{base}/documents/{doc_id}?type={doc_type}"
