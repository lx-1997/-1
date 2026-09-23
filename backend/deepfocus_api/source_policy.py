"""资讯来源策略。

生产库真实字段口径：``lxaa*`` 是 TradeAlpha/lxaa 快讯源的上游编号；
富途财讯使用数字编号，并由 ``backend.futoucaixin.cn`` 原文地址识别。
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any


_TRADEALPHA_RE = re.compile(
    r"(?:trade\s*[-_ ]?\s*alpha|alpha\.lxaa\.top)",
    re.IGNORECASE,
)
_FUTOUCAIXIN_RE = re.compile(
    r"(?:futoucaixin|backend\.futoucaixin\.cn|斧头财信|斧头财经)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _metadata_values(metadata: Any, *, depth: int = 0) -> list[str]:
    """提取上游元数据中的来源值，兼容 metadata/source 嵌套对象。"""
    if depth > 3 or metadata is None:
        return []
    if isinstance(metadata, Mapping):
        values: list[str] = []
        for key, value in metadata.items():
            key_text = _text(key).casefold()
            normalized_key = re.sub(r"^(?:upstream|raw|original)[_-]", "", key_text)
            if normalized_key in {
                "source", "source_name", "source_url", "provider", "publisher",
                "origin", "origin_name", "origin_url", "feed", "feed_name",
                "channel", "channel_name", "domain", "site", "site_name",
                "name", "label", "title", "url",
            }:
                values.append(_text(value))
            if isinstance(value, Mapping):
                values.extend(_metadata_values(value, depth=depth + 1))
        return values
    if isinstance(metadata, (list, tuple, set)):
        values = []
        for value in metadata:
            values.extend(_metadata_values(value, depth=depth + 1))
        return values
    return []


def _source_text(
    *,
    source_id: Any = None,
    source_name: Any = None,
    source_type: Any = None,
    url: Any = None,
    metadata: Any = None,
    title: Any = None,
    content: Any = None,
) -> str:
    parts = [_text(source_id), _text(source_name), _text(source_type), _text(url)]
    parts.extend(_metadata_values(metadata))
    # 标题/正文只作为最后一道兼容兜底：上游已经把品牌写入内容时，仍应能拦截；
    # 入库前会先调用本函数，再执行 news_filter.scrub，因此不会被抹词影响判定。
    parts.extend([_text(title), _text(content)])
    return "\n".join(part for part in parts if part)


def is_tradealpha_source(
    *,
    source_id: Any = None,
    source_name: Any = None,
    source_type: Any = None,
    url: Any = None,
    metadata: Any = None,
    title: Any = None,
    content: Any = None,
) -> bool:
    """是否来自已停用的 TradeAlpha/lxaa 资讯源。"""
    if _text(source_id).casefold().startswith("lxaa"):
        return True
    return bool(_TRADEALPHA_RE.search(_source_text(
        source_id=source_id,
        source_name=source_name,
        source_type=source_type,
        url=url,
        metadata=metadata,
        title=title,
        content=content,
    )))


def is_futoucaixin_source(
    *,
    source_id: Any = None,
    source_name: Any = None,
    source_type: Any = None,
    url: Any = None,
    metadata: Any = None,
    title: Any = None,
    content: Any = None,
) -> bool:
    """识别富途财讯来源，不再把 ``lxaa*`` 误判为富途。"""
    return bool(_FUTOUCAIXIN_RE.search(_source_text(
        source_id=source_id,
        source_name=source_name,
        source_type=source_type,
        url=url,
        metadata=metadata,
        title=title,
        content=content,
    )))


def tradealpha_blocking_enabled() -> bool:
    """运行时读取开关，确保 ``load_dotenv`` 后的配置立即生效。"""
    raw = os.getenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")
    return raw.strip().casefold() not in {"0", "false", "no", "off"}
