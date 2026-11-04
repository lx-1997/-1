"""Read-only cache availability; never downloads sources or invokes a model."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .auth import optional_current_user
from .research_cache import deep_draft_cache_key, legacy_deep_draft_cache_key, is_valid_deep_draft_cache
from .research_digest import ResearchDeepDraftRequest

router = APIRouter()


class StatusEntry(BaseModel):
    key: str = Field(max_length=1000)
    kind: Literal['report', 'news']
    file_id: str = Field(default='', max_length=500)
    filename: str = Field(default='', max_length=500)
    out: str = Field(default='downloads/海外投行报告', max_length=500)
    title: str = Field(default='', max_length=2000)
    max_pages: int = Field(default=18, ge=1, le=60)
    message_id: str = Field(default='', max_length=500)


class StatusRequest(BaseModel):
    entries: list[StatusEntry] = Field(default_factory=list, max_length=100)


@router.post('/api/ai/interpretation-status')
async def interpretation_status(request: StatusRequest, _user=Depends(optional_current_user)):
    from . import main
    from .metrics_store import get_ai_cache_many

    keys = {}
    for entry in request.entries:
        if entry.kind == 'report':
            source = ResearchDeepDraftRequest(
                title=entry.title, file_id=entry.file_id or None,
                workbench_filename=entry.filename or None, workbench_out=entry.out,
                max_pages=entry.max_pages,
            )
            keys[entry.key] = [deep_draft_cache_key(source), legacy_deep_draft_cache_key(source)]
    cached = await asyncio.to_thread(get_ai_cache_many, [key for group in keys.values() for key in group if key])
    statuses = {entry.key: False for entry in request.entries}
    for key, group in keys.items():
        statuses[key] = any(is_valid_deep_draft_cache(cached.get(ref)) for ref in group)

    # Use the server's stored source and version alias, never caller-supplied body.
    for entry in request.entries:
        if entry.kind != 'news' or not entry.message_id:
            continue
        message = await asyncio.to_thread(main.get_realtime_message, entry.message_id)
        if message is None:
            continue
        title = (message.title or '').strip()
        if (message.topic or '') == '文章':
            alias = await main._news_source_alias_key(message, title)
            statuses[entry.key] = await main._get_news_source_cached(alias, title) is not None
        else:
            content = (message.content or '').strip()
            ref = 'news:v5:' + hashlib.sha1(f'{title}\n{content}'.encode()).hexdigest()[:20]
            value = await asyncio.to_thread(main.metrics_get_ai_cache, ref)
            statuses[entry.key] = not main.news_result_needs_refresh(value, title)
    return {'statuses': statuses}
