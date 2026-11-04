import pytest
from pydantic import ValidationError

from deepfocus_api import main, metrics_store
from deepfocus_api.interpretation_status import StatusEntry, StatusRequest, interpretation_status
from deepfocus_api.research_cache import deep_draft_cache_key
from deepfocus_api.schemas import ResearchDeepDraftRequest, ResearchDeepDraftResponse


@pytest.mark.asyncio
async def test_status_accepts_full_draft_but_rejects_fallback_and_never_generates(monkeypatch):
    full = ResearchDeepDraftResponse(
        provider='test', sections=[{'id': 'facts', 'title': '事实', 'paragraphs': ['完整解读。']}],
    ).model_dump(mode='json')
    sources = {name: deep_draft_cache_key(ResearchDeepDraftRequest(file_id=name)) for name in ('ready', 'fallback')}
    values = {sources['ready']: full, sources['fallback']: {**full, 'provider': 'text-only-fallback'}}
    monkeypatch.setattr(metrics_store, 'get_ai_cache_many', lambda refs: values)
    def forbidden(*args, **kwargs):
        raise AssertionError('Availability must not extract a source or run a model')
    monkeypatch.setattr(main, '_resolve_research_pdf_bytes', forbidden)
    request = StatusRequest(entries=[StatusEntry(key=name, kind='report', file_id=name) for name in ('ready', 'fallback', 'missing')])
    assert await interpretation_status(request, None) == {'statuses': {'ready': True, 'fallback': False, 'missing': False}}


def test_status_batch_is_bounded():
    with pytest.raises(ValidationError):
        StatusRequest(entries=[StatusEntry(key=str(i), kind='report') for i in range(101)])
