"""Evidence-bound annual planned sessions, separate from actual security status.

This retrospective check detects dates omitted by every price series. Annual
notices do not certify exceptional closures, intraday events, or suspensions.
"""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .quant_research_engine import digest, validate_dataset

BJ = timezone(timedelta(hours=8))
SCHEMA = 'reviewed-annual-exchange-schedule-v1'
FACT_FIELDS = ('exchange', 'year', 'closed_ranges', 'resumes_on', 'weekend_makeup_days_closed')


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored-1)
    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def notice_body(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 2*1024*1024:
        raise ValueError('Missing or oversized official notice')
    parser = _Text()
    parser.feed(raw.decode('utf-8', errors='strict'))
    plain = re.sub(r'\s+', '', ''.join(parser.parts))
    start = plain.index('一、休市安排')
    return plain[start:plain.index('二、', start)]


def stamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Evidence clocks require a timezone')
    return value


def facts_digest(record):
    return digest({k: record[k] for k in FACT_FIELDS})


def validate_manifest(manifest, sources, *, as_of):
    if (manifest.get('schema') != SCHEMA or manifest.get('scope') != 'annual_planned_sessions_only'
            or manifest.get('unexpected_closure_search_complete') is not False
            or manifest.get('historical_point_in_time_verified') is not False):
        raise ValueError('Unsupported calendar scope or completeness claim')
    clock = stamp(as_of)
    records = manifest['records']
    years = manifest['years']
    if (not years or len(years) > 10 or any(type(y) is not int for y in years)
            or years != list(range(min(years), max(years)+1))):
        raise ValueError('Nonempty consecutive reviewed years required')
    required = {(e, y) for e in ('SSE', 'SZSE') for y in years}
    if len(records) != len(required) or {(r['exchange'], r['year']) for r in records} != required:
        raise ValueError('Missing or duplicate exchange/year notices')
    for record in records:
        year, exchange = record['year'], record['exchange']
        allowed = {'SSE': {'www.sse.com.cn'}, 'SZSE': {'www.szse.cn', 'investor.szse.cn'}}[exchange]
        url = urlsplit(record['url'])
        if (url.scheme != 'https' or url.netloc not in allowed or url.query or url.fragment
                or record['review_method'] != 'assistant_read_notice_body_and_cross_exchange_comparison'):
            raise ValueError('Unreviewed or unofficial calendar source')
        if not stamp(record['available_at']) <= stamp(record['observed_at']) <= stamp(record['reviewed_at']) <= clock:
            raise ValueError('Invalid calendar evidence clocks')
        raw = sources.get(record['source_sha256'])
        if raw is None or hashlib.sha256(raw).hexdigest() != record['source_sha256']:
            raise ValueError('Official calendar source missing or changed')
        if hashlib.sha256(notice_body(raw).encode()).hexdigest() != record['body_sha256']:
            raise ValueError('Reviewed notice body changed')
        if facts_digest(record) != record['reviewed_facts_digest']:
            raise ValueError('Reviewed calendar facts changed')
        ranges = record['closed_ranges']
        if not ranges or len(ranges) != len(record['resumes_on']):
            raise ValueError('Missing closure/reopening facts')
        previous_end = ''
        for (a, b), resume in zip(ranges, record['resumes_on']):
            first, last, reopened = date.fromisoformat(a), date.fromisoformat(b), date.fromisoformat(resume)
            if (not previous_end < a <= b or first < date(year-1, 12, 25)
                    or last > date(year, 12, 31) or (last-first).days > 15):
                raise ValueError('Invalid or overlapping closure range')
            expected = last+timedelta(days=1)
            while expected.weekday() >= 5:
                expected += timedelta(days=1)
            if reopened != expected:
                raise ValueError('Reopening fact disagrees with closure/weekend rule')
            previous_end = b
        for day in record['weekend_makeup_days_closed']:
            d = date.fromisoformat(day)
            if d.year != year or d.weekday() < 5:
                raise ValueError('Government makeup workdays must not open a weekend session')
    for year in years:
        pair = [r for r in records if r['year'] == year]
        for field in ('closed_ranges', 'resumes_on', 'weekend_makeup_days_closed'):
            if pair[0][field] != pair[1][field]:
                raise ValueError('Reviewed exchange schedules disagree; do not intersect them')
    return manifest


def planned_sessions(manifest, start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b or any(y not in manifest['years'] for y in range(a.year, b.year+1)):
        raise ValueError('Requested calendar interval is outside reviewed years')
    ranges = [pair for r in manifest['records'] if r['exchange'] == 'SSE'
              for pair in r['closed_ranges']]
    days = []
    while a <= b:
        d = a.isoformat()
        if a.weekday() < 5 and not any(first <= d <= last for first, last in ranges):
            days.append(d)
        a += timedelta(days=1)
    return days


def compare_dates(manifest, observed, start, end):
    if observed != sorted(set(observed)):
        raise ValueError('Observed dates must be unique and ordered')
    for day in observed:
        date.fromisoformat(day)
    expected = planned_sessions(manifest, start, end)
    missing, extra = sorted(set(expected)-set(observed)), sorted(set(observed)-set(expected))
    return {'status': 'matches_plan' if not missing and not extra else 'rejected',
            'start': start, 'end': end, 'expected_sessions': len(expected),
            'observed_sessions': len(observed), 'expected_dates_digest': digest(expected),
            'observed_dates_digest': digest(observed), 'missing_planned_sessions': missing,
            'unexpected_sessions': extra, 'annual_notice_plan_checked': True,
            'actual_exchange_sessions_certified': False, 'security_status_verified': False,
            'historical_point_in_time_verified': False}


def check_dataset(manifest, dataset, start, end, *, as_of):
    validate_dataset(dataset)
    clock = stamp(as_of)
    clocks = {s: stamp(p['fetched_at']) for s, p in dataset.items()}
    if any(c > clock for c in clocks.values()):
        raise ValueError('Future price capture clock')
    # Same conservative completed-session policy as the research ingestion.
    cutoffs = []
    for value in clocks.values():
        local = value.astimezone(BJ)
        cutoff = local.date() if local.time() >= time(16, 30) else local.date()-timedelta(days=1)
        cutoffs.append(cutoff)
    cutoff = min([date.fromisoformat(end), *cutoffs]).isoformat()
    comparisons = {s: compare_dates(manifest, [b['date'] for b in p['bars']], start, cutoff)
                   for s, p in dataset.items()}
    return {'requested_start': start, 'requested_end': end, 'completed_through': cutoff,
            'completion_policy': 'minimum_capture_clock_beijing_1630',
            'calendar_manifest_digest': digest(manifest), 'symbols': comparisons,
            'status': 'matches_plan' if all(r['status']=='matches_plan' for r in comparisons.values()) else 'rejected',
            'promotion_allowed': False}
