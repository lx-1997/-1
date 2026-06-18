from __future__ import annotations

import html
import ipaddress
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import HTTPException, status

from .file_tools import MAX_UPLOAD_BYTES, extract_file_bytes


REPORT_URL_TIMEOUT = httpx.Timeout(24.0, connect=8.0, read=18.0)
REPORT_URL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
}


@dataclass(frozen=True)
class RemoteReportExtraction:
    url: str
    final_url: str
    filename: str
    title: str
    content_type: Optional[str]
    text: str
    parser: str
    char_count: int
    truncated: bool


async def extract_report_url(
    url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> RemoteReportExtraction:
    normalized_url = _validate_report_url(url)
    if client:
        return await _extract_with_client(client, normalized_url)

    async with httpx.AsyncClient(
        timeout=REPORT_URL_TIMEOUT,
        follow_redirects=True,
        headers=REPORT_URL_HEADERS,
    ) as scoped_client:
        return await _extract_with_client(scoped_client, normalized_url)


async def _extract_with_client(client: httpx.AsyncClient, url: str) -> RemoteReportExtraction:
    try:
        async with client.stream("GET", url, headers=REPORT_URL_HEADERS, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"研报 URL 下载失败：HTTP {response.status_code}",
                )
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="远程文件过大，当前限制为 12MB。",
                )
            raw = await _read_limited(response)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"研报 URL 下载失败：{exc}") from exc

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or None
    final_url = str(response.url)
    filename = _filename_from_response(final_url, content_type, response.headers.get("content-disposition"))
    if _looks_like_html(raw, content_type, filename):
        title = _html_title(raw) or _title_from_filename(filename)
        text, truncated = _html_to_text(raw)
        if len(text.strip()) < 30:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="页面正文过短，无法作为研报入库。")
        return RemoteReportExtraction(
            url=url,
            final_url=final_url,
            filename=filename,
            title=title,
            content_type=content_type or "text/html",
            text=text,
            parser="html",
            char_count=len(text),
            truncated=truncated,
        )

    extracted = extract_file_bytes(raw, filename=filename, content_type=content_type)
    return RemoteReportExtraction(
        url=url,
        final_url=final_url,
        filename=extracted.filename,
        title=_title_from_filename(extracted.filename),
        content_type=extracted.content_type,
        text=extracted.text,
        parser=extracted.parser,
        char_count=extracted.char_count,
        truncated=extracted.truncated,
    )


async def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="远程文件过大，当前限制为 12MB。",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_report_url(value: str) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="研报 URL 必须是 http(s) 地址。")
    host = parsed.hostname.strip().lower()
    if host in {"localhost"} or host.endswith(".local"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持本机或内网研报 URL。")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不支持本机或内网研报 URL。")
    return url


def _filename_from_response(url: str, content_type: Optional[str], content_disposition: Optional[str]) -> str:
    filename = _filename_from_content_disposition(content_disposition)
    if not filename:
        path_name = Path(unquote(urlparse(url).path or "")).name
        filename = path_name or "remote-report"
    filename = re.sub(r"[\\/:*?\"<>|]+", "_", filename).strip() or "remote-report"
    if "." not in Path(filename).name:
        extension = mimetypes.guess_extension(content_type or "") or ".html"
        filename = f"{filename}{extension}"
    return filename


def _filename_from_content_disposition(value: Optional[str]) -> str:
    if not value:
        return ""
    star_match = re.search(r"filename\*=([^']*)''([^;]+)", value, re.I)
    if star_match:
        return Path(unquote(star_match.group(2).strip().strip('"'))).name
    match = re.search(r'filename="?([^";]+)"?', value, re.I)
    return Path(unquote(match.group(1).strip())).name if match else ""


def _looks_like_html(raw: bytes, content_type: Optional[str], filename: str) -> bool:
    lowered_type = (content_type or "").lower()
    if "html" in lowered_type:
        return True
    if Path(filename).suffix.lower() in {".html", ".htm"}:
        return True
    head = raw[:2048].lower()
    return b"<html" in head or b"<!doctype html" in head


def _html_title(raw: bytes) -> str:
    text = _decode_text(raw[:120_000])
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    return _clean_text(_strip_tags(match.group(1)))[:180] if match else ""


def _html_to_text(raw: bytes) -> tuple[str, bool]:
    text = _decode_text(raw)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<(script|style|noscript|svg|canvas)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</(p|div|li|tr|h\d|section|article|br)>", "\n", text, flags=re.I)
    cleaned = _clean_text(_strip_tags(text))
    return cleaned[:80_000], len(cleaned) > 80_000


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _clean_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem or filename
    return re.sub(r"[_\-]+", " ", stem).strip() or "远程研报"
