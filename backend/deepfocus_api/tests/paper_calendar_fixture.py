"""Calendar-aware synthetic fixtures for paper-flow tests, never production data."""
import copy
from deepfocus_api import quant_calendar_store as evidence, quant_calendar_service as calendar
from deepfocus_api import quant_research_store as jobs
from deepfocus_api.quant_exchange_schedule import planned_sessions
from deepfocus_api.tests.test_quant_research_loop import dataset, request


def aligned(reviewed, count=None):
    manifest, _ = reviewed
    dates = planned_sessions(manifest, '2023-01-01', '2024-08-09')
    if count is not None: dates=dates[:count]
    data=dataset(len(dates))
    for payload in data.values():
        payload['fetched_at']=jobs.now()
        for bar,day in zip(payload['bars'],dates):bar['date']=day
    return data


async def approve(owner, identity, reviewed, key='mandatory-calendar'):
    manifest,sources=copy.deepcopy(reviewed)
    bundle=evidence.put(owner,'synthetic calendar fixture',manifest,sources)
    job=await calendar.submit(owner,identity,bundle['id'],key)
    task=calendar.lifecycle._tasks.get(job['id'])
    if task:await task
    result=jobs.get(owner,job['id'])
    assert result['status']=='completed' and result['result']['quality']['status']=='matches_plan'
    return result
