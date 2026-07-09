from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

_MANIFEST = "manifest.jsonl"


def collection_log_root() -> Path:
    root = Path(settings.collection_log_dir)
    if root.is_absolute():
        return root
    backend_dir = Path(__file__).resolve().parents[1]
    return (backend_dir / root).resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned[:80] or "unknown"


def save_collection_snapshot(
    *,
    source: str,
    payload: dict[str, Any],
    edinet_code: str | None = None,
    fresh: bool = True,
    error: str | None = None,
) -> str | None:
    """Persist a fetched payload under data/collection-logs and append manifest."""
    if not settings.collection_log_enabled:
        return None

    fetched_at = payload.get("fetched_at") or _now_iso()
    stamp = fetched_at.replace(":", "").replace("+00:00", "Z")
    day = fetched_at[:10]
    code_part = _safe_slug(edinet_code or payload.get("edinet_code") or "global")
    root = collection_log_root()
    target_dir = root / _safe_slug(source) / day
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{code_part}_{stamp}.json"
    path = target_dir / filename

    record = {
        "logged_at": _now_iso(),
        "source": source,
        "edinet_code": edinet_code or payload.get("edinet_code"),
        "fresh": fresh,
        "error": error or payload.get("error"),
        "payload": payload,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_entry = {
        "logged_at": record["logged_at"],
        "source": source,
        "edinet_code": record["edinet_code"],
        "fresh": fresh,
        "error": record["error"],
        "path": str(path.relative_to(root)),
        "count": payload.get("count"),
        "rss_total": payload.get("rss_total"),
        "days": payload.get("days"),
        "keyword": payload.get("keyword") or payload.get("query"),
        "command": payload.get("command"),
    }
    with (root / _MANIFEST).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
    return str(path)


def record_sync_snapshot(command: str, summary: dict[str, Any] | None = None) -> str | None:
    payload = {
        "command": command,
        "fetched_at": _now_iso(),
        "summary": summary or {},
    }
    return save_collection_snapshot(
        source="edinet_sync",
        edinet_code=None,
        payload=payload,
        fresh=True,
    )


def read_collection_manifest(
    *,
    source: str | None = None,
    edinet_code: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    root = collection_log_root()
    manifest_path = root / _MANIFEST
    if not manifest_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if source and row.get("source") != source:
            continue
        if edinet_code and row.get("edinet_code") != edinet_code:
            continue
        rows.append(row)
    return rows[-limit:][::-1]


def record_external_media_delta(
    *,
    source: str,
    edinet_code: str,
    stats: dict[str, Any],
    error: str | None = None,
) -> None:
    """Append a compact manifest line when new external-media rows are stored."""
    if not settings.collection_log_enabled:
        return
    root = collection_log_root()
    root.mkdir(parents=True, exist_ok=True)
    manifest_entry = {
        "logged_at": _now_iso(),
        "source": source,
        "edinet_code": edinet_code,
        "fresh": True,
        "error": error,
        "path": None,
        "stats": stats,
    }
    with (root / _MANIFEST).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
