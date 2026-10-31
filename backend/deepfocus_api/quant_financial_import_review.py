"""New-import PDF preflight only; archived store readers remain unchanged.

Reject known summary covers/unreadable evidence and impossible page references.
Absence of a cover marker is not a full-report or financial-value certificate.
"""
import copy
from datetime import datetime,timezone
import hashlib,re,time
from pathlib import Path
from . import quant_financial_vintages as vintages

VERSION='financial-import-cover-preflight-v1'


class PreflightRejected(ValueError):
    def __init__(self,code,document_ids):
        if code not in {'summary_cover','unreadable_cover','invalid_pdf','invalid_page_reference','encrypted_or_page_count','cover_size'}:
            raise ValueError('Unknown import preflight code')
        super().__init__(code)
        self.preflight_code=code
        self.document_ids=tuple(document_ids)


def _pins():
    return {**vintages.source_pins(),'quant_financial_import_review.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS=_pins()


def require_source():
    vintages.require_current_source()
    if _pins()!=LOADED_PINS:raise ValueError('Loaded import preflight source changed')


def review(manifest,sources):
    require_source();manifest=copy.deepcopy(manifest);sources=dict(sources)
    records=vintages.validate_manifest(manifest,sources)
    groups={}
    for record in records:groups.setdefault(record.source_sha256,[]).append(record)
    if len(groups)>32:raise ValueError('Import preflight exceeds 32 distinct PDFs')
    # Lazy dependency: no effect on archive read routes or API startup.
    import fitz
    deadline=time.monotonic()+5;documents=[]
    for sha,rows in groups.items():
        if time.monotonic()>=deadline:raise TimeoutError('Cooperative import preflight exhausted')
        ids=[record.document_id for record in rows]
        try:
            with fitz.open(stream=sources[sha],filetype='pdf') as doc:
                if doc.needs_pass or not 1<=len(doc)<=1000:raise PreflightRejected('encrypted_or_page_count',ids)
                rect=doc[0].rect
                if not 0<rect.width<=3000 or not 0<rect.height<=3000:raise PreflightRejected('cover_size',ids)
                text=doc[0].get_text();raw=text.encode()
                if not text.strip() or len(raw)>1024**2:raise PreflightRejected('unreadable_cover',ids)
                if re.search(r'(?:年度|季度)报告摘要',re.sub(r'\s+','',text)):
                    raise PreflightRejected('summary_cover',ids)
                for record in rows:
                    pages=[record.source_page,*((record.metric_source_pages or {}).values())]
                    if any(page>len(doc) for page in pages):raise PreflightRejected('invalid_page_reference',ids)
                documents.append({'source_sha256':sha,'document_ids':[x.document_id for x in rows],
                    'page_count':len(doc),'first_page_text_sha256':hashlib.sha256(raw).hexdigest(),
                    'summary_marker_detected':False,'declared_parent_page_references_in_range':True})
        except (ValueError,TimeoutError):raise
        except Exception as exc:raise PreflightRejected('invalid_pdf',ids) from exc
        if time.monotonic()>=deadline:raise TimeoutError('Cooperative import preflight exhausted')
    require_source()
    result={'schema':VERSION,'documents':documents,'manifest_digest':vintages.digest(manifest),
            'source_pins':dict(LOADED_PINS),'PyMuPDF_version':fitz.VersionBind,
            'completed_at':datetime.now(timezone.utc).isoformat(),
            'scope':'first_page_summary_marker_readability_and_declared_parent_page_bounds',
            'full_report_or_financial_facts_certified':False,'stored_archive_rewritten':False,
            'externally_attested_time':False,'hard_native_CPU_or_RSS_certified':False,
            'promotion_allowed':False,'commercial_ready':False}
    result['review_digest']=vintages.digest(result)
    return result
