"""Attribute reports from their original cover, using the existing PDF cache."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Any

from . import db

DB_PATH = db.data_path(".research_publishers.sqlite3", "DEEPFOCUS_RESEARCH_PUBLISHERS_DB_PATH")
PDF_CACHE = Path(os.getenv("PDF_CACHE_DIR", "/opt/deepfocus/pdf_cache"))
RULE_VERSION = 1

# The patterns are anchored to a masthead line, not mentions in body text.
# Some Goldman covers split the logo over two lines and OCR drops initials.
_MASTHEADS = {
    "高盛": r"(?:goldman\s+sachs(?:\s+research)?|goldman\s+research\s+(?:equity research\s+)?sachs|高盛全球投资研究|高盛研究)",
    "摩根士丹利": r"(?:morgan\s*stanley(?:\s*\|?\s*research)?|morgan stanley (?:asia|taiwan|& co\.)[\w .&+()]*|摩根士丹利研究)",
    "花旗": r"(?:(?:(?:flash|viewpoint|action|essentials)\s*\|\s*)?citi\s+resear(?:ch)?|citigroup global markets(?: inc\.?)?|花旗研究)",
    "摩根大通": r"(?:j\s*p\s*m\s*o\s*r\s*g\s*a\s*n|j\.?\s*p\.?\s*morgan(?: research)?|jpmorgan chase bank[\w .,&()]*|摩根大通研究)",
    "伯恩斯坦": r"(?:bernstein(?: research)?|伯恩斯坦研究)",
    "野村": r"(?:nomura(?: research| securities(?: co\.,? ltd\.?)?)?|noaura\s+global markets research|野村证券)",
    "瑞银": r"(?:ubs(?: global research| investment bank| securities(?: llc)?)?|瑞银证券|瑞银研究)",
    "美银": r"(?:bofa (?:global research|securities)|bank of america global research|美银证券)",
    "德意志银行": r"(?:deutsche bank(?: research| securities inc\.?)?|德意志银行研究)",
    "巴克莱": r"(?:barclays(?: research| capital inc\.?)?|巴克莱研究)",
    "汇丰": r"(?:hsbc global (?:research|investment research)|汇丰环球研究)",
    "富国银行": r"(?:wells fargo (?:securities|equity research))",
    "Jefferies": r"(?:jefferies(?: equity research| llc)?)",
    "Evercore ISI": r"evercore\s+isi",
    "SemiAnalysis": r"semi\s*analysis",
    "Culper Research": r"culper research",
}
_COVER_PATTERNS = {
    publisher: re.compile(rf"^\s*{pattern}\s*$", re.IGNORECASE | re.MULTILINE)
    for publisher, pattern in _MASTHEADS.items()
}
_GOLDMAN_OCR = re.compile(
    r"^\s*[fgj]?oldman\s+researc[hr]\s+"
    r"(?:(?:equity|economics|commodities|global investment) research\s+)?"
    r"[js]?achs(?=\s*$|[ \t]+\d{1,2}\s+[A-Za-z]+\s+20\d{2})",
    re.IGNORECASE | re.MULTILINE,
)
_GENERIC = {"", "海外投行", "海外投行研报", "海外投行报告", "投行研报", "稻草财经", "daocaijing", "未知", "未标明来源"}


def _cover_publishers(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text)
    publishers = {name for name, pattern in _COVER_PATTERNS.items() if pattern.search(normalized)}
    if _GOLDMAN_OCR.search(normalized):
        publishers.add("高盛")
    return publishers


def _inspect_cover(path: Path) -> tuple[str, str]:
    import fitz

    with fitz.open(path) as doc:
        if not len(doc):
            return "", "empty"
        page = doc[0]
        cover = page.rect
        header = fitz.Rect(cover.x0, cover.y0, cover.x1, cover.y0 + cover.height * 0.21)
        publishers = _cover_publishers(page.get_text(clip=header))
        if len(publishers) == 1:
            return publishers.pop(), "pdf_masthead"
        if publishers:
            return "", "conflict"
        tesseract = shutil.which("tesseract")
        if not tesseract or not page.get_images():
            return "", "no_masthead"
        scale = min(2.0, 1500 / max(header.width, header.height, 1))
        image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=header, colorspace=fitz.csGRAY)
        # Supported OCR mastheads are Latin names. Chinese mastheads use the
        # PDF text layer, avoiding a larger OCR model on the shared host.
        command = [tesseract, "stdin", "stdout", "-l", "eng", "--psm", "11"]
        if nice := shutil.which("nice"):
            command = [nice, "-n", "15", *command]
        result = subprocess.run(
            command, input=image.tobytes("png"), capture_output=True, timeout=7,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"}, check=False,
        )
        if result.returncode:
            return "", "ocr_failed"
        publishers = _cover_publishers(result.stdout.decode(errors="replace"))
        if len(publishers) == 1:
            return publishers.pop(), "ocr_masthead"
        return "", "conflict" if publishers else "no_masthead"


def _file_id(row: dict[str, Any]) -> str:
    value = str(row.get("file_id") or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else ""


def index_cached_publishers(rows: list[dict[str, Any]], *, limit: int = 6) -> dict[str, int]:
    """Scan a bounded batch, without downloading PDFs or using the request path."""
    stats = {"scanned": 0, "identified": 0}
    with closing(db.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_publishers (
                file_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                signature TEXT NOT NULL,
                publisher TEXT NOT NULL,
                evidence TEXT NOT NULL
            )
        """)
        seen = {row[0]: row[1] for row in conn.execute(
            "SELECT file_id, signature FROM research_publishers WHERE version=?", (RULE_VERSION,),
        )}
        for row in rows:
            if stats["scanned"] >= limit:
                break
            file_id = _file_id(row)
            if not file_id:
                continue
            pdf = PDF_CACHE / f"fid_raw_{file_id}.pdf"
            try:
                stat = pdf.stat()
            except OSError:
                continue
            signature = f"{stat.st_size}:{stat.st_mtime_ns}"
            if seen.get(file_id) == signature:
                continue
            try:
                publisher, evidence = _inspect_cover(pdf)
            except Exception:  # MuPDF uses its own error types for corrupt or oversized files.
                publisher, evidence = "", "unreadable"
            try:
                current = pdf.stat()
            except OSError:
                continue
            if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                continue
            with conn:
                conn.execute("""
                    INSERT INTO research_publishers VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET version=excluded.version,
                        signature=excluded.signature, publisher=excluded.publisher, evidence=excluded.evidence
                """, (file_id, RULE_VERSION, signature, publisher, evidence))
            seen[file_id] = signature
            stats["scanned"] += 1
            stats["identified"] += bool(publisher)
    return stats


def _index_new_cached_publishers() -> dict[str, int]:
    candidates = []
    for path in PDF_CACHE.glob("fid_raw_*.pdf"):
        try:
            candidates.append((path.stat().st_mtime_ns, path.stem.removeprefix("fid_raw_")))
        except OSError:
            continue
    candidates.sort(reverse=True)
    return index_cached_publishers([{"file_id": file_id} for _, file_id in candidates], limit=1)


def index_new_cached_publishers() -> dict[str, int]:
    """Keep PDF rendering and OCR out of the long-lived API process."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        available = re.search(r"^MemAvailable:\s+(\d+)", meminfo.read_text(), re.MULTILINE)
        if available and int(available.group(1)) < 550 * 1024:
            return {"scanned": 0, "identified": 0}
    command = [sys.executable, "-m", "deepfocus_api.research_publishers"]
    if nice := shutil.which("nice"):
        command = [nice, "-n", "15", *command]
    result = subprocess.run(command, capture_output=True, timeout=18, check=True)
    return json.loads(result.stdout.decode().splitlines()[-1])


def with_publishers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlay publisher evidence without replacing source records on refresh."""
    ids = sorted({_file_id(row) for row in rows} - {""})
    publishers: dict[str, str] = {}
    if ids and DB_PATH.is_file():
        try:
            with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    publishers.update(conn.execute(
                        f"SELECT file_id, publisher FROM research_publishers "
                        f"WHERE version=? AND publisher<>'' AND file_id IN ({placeholders})",
                        (RULE_VERSION, *batch),
                    ))
        except sqlite3.Error:
            pass
    enriched = []
    for row in rows:
        source = str(row.get("org") or "").strip()
        publisher = publishers.get(_file_id(row)) or ("" if source.casefold() in _GENERIC else source)
        enriched.append({**row, "org": publisher})
    return enriched


if __name__ == "__main__":
    import fcntl
    import resource

    # PyMuPDF maps about 300 MiB before reading a document. Limit writable
    # allocations separately from the larger address space needed by its libraries.
    resource.setrlimit(resource.RLIMIT_AS, (448 * 1024**2, 448 * 1024**2))
    resource.setrlimit(resource.RLIMIT_DATA, (128 * 1024**2, 128 * 1024**2))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DB_PATH.with_suffix(".scan.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"scanned": 0, "identified": 0}))
        else:
            print(json.dumps(_index_new_cached_publishers()))
