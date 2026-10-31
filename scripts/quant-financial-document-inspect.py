#!/usr/bin/env python3
"""Offline diagnostics for explicitly captured same-day PDF candidates.

Hashes and cover markers never select a canonical report or infer a revision.
PyMuPDF is a research-tool dependency; no API server integration or network.
"""
import argparse
from datetime import datetime,timezone,timedelta
import hashlib,json,os,re,tempfile,time
from pathlib import Path
import fitz

VERSION='financial-same-day-PDF-diagnostics-v1'
LIMITS={'documents':8,'pdf_bytes':10*1024**2,'total_bytes':24*1024**2,'pages_per_document':1000,
        'total_pages':2000,'text_bytes':32*1024**2,'page_pixels':9000000,'cooperative_seconds':60}


def sha(raw):return hashlib.sha256(raw).hexdigest()
def clock(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Explicit capture timezone required')
    return d.astimezone(timezone.utc)


def inspect_pdf(raw,deadline,budget):
    if time.monotonic()>=deadline:raise TimeoutError('Document review budget exhausted')
    with fitz.open(stream=raw,filetype='pdf') as doc:
        if doc.needs_pass or not 1<=len(doc)<=1000:raise ValueError('Unsupported encrypted/empty/oversized PDF')
        budget['pages']+=len(doc)
        if budget['pages']>2000:raise ValueError('Total page budget exhausted')
        exact=hashlib.sha256();normalized=hashlib.sha256();pages=[];cover=''
        for n,page in enumerate(doc,1):
            if time.monotonic()>=deadline:raise TimeoutError('Document review budget exhausted')
            rect=page.rect
            if not 0<rect.width<=3000 or not 0<rect.height<=3000 or rect.width*rect.height>9000000:raise ValueError('Page raster bounds exceeded')
            text=page.get_text();encoded=text.encode();budget['text']+=len(encoded)
            if budget['text']>32*1024**2:raise ValueError('Text budget exhausted')
            if n==1:cover=re.sub(r'\s+','',text)
            # Diagnostic only: removing whitespace can join numbers. It is not
            # used to extract values or establish report/economic equivalence.
            compact=re.sub(r'\s+','',text).encode();exact.update(encoded);normalized.update(compact)
            pix=page.get_pixmap(matrix=fitz.Matrix(1,1),colorspace=fitz.csRGB,alpha=False,annots=True)
            pages.append({'page':n,'text_bytes':len(encoded),'text_sha256':sha(encoded),
                          'whitespace_removed_text_sha256':sha(compact),'pixel_width':pix.width,
                          'pixel_height':pix.height,'RGB72_with_annotations_sha256':sha(pix.samples)})
        return {'page_count':len(doc),'first_page_summary_marker':bool(re.search(r'(?:年度|季度)报告摘要',cover)),
                'text_sha256':exact.hexdigest(),'whitespace_removed_text_sha256':normalized.hexdigest(),
                'empty_text_pages':[x['page'] for x in pages if x['text_bytes']==0],'pages':pages}


def review(workspace):
    source_sha=sha(Path(__file__).read_bytes());workspace=workspace.resolve();manifest_file=workspace/'capture-manifest.json'
    with manifest_file.open('rb') as f:raw=f.read(262145)
    if len(raw)>262144:raise ValueError('Capture manifest exceeds 256KiB')
    m=json.loads(raw);plan=m['plan'];caps=m['captures'];registered=clock(plan['registered_at']);finished=clock(m['finished_at'])
    if not 2<=len(caps)<=8 or len(caps)!=len(plan['documents']) or plan['requests']!=len(caps) or plan['automatic_retries']!=0:raise ValueError('Explicit bounded complete capture required')
    if finished>datetime.now(timezone.utc) or registered>finished:raise ValueError('Invalid capture clock')
    inputs=[];used=0;identities=set();groups={}
    for cap,expected in zip(caps,plan['documents']):
        if any(cap.get(k)!=v for k,v in expected.items()):raise ValueError('Capture differs from registered document')
        code=cap['symbol'];doc=cap['document_id'];date=cap['published_date'];period=cap['period']
        if (not re.fullmatch(r'[0-9]{6}',code) or not re.fullmatch(r'[0-9]{1,20}',doc)
            or not re.fullmatch(r'finalpage/'+re.escape(date)+'/'+doc+r'\.(?:PDF|pdf)',cap['exact_adjunct_path'])
            or cap['URL']!='https://static.cninfo.com.cn/'+cap['exact_adjunct_path']):raise ValueError('Exact issuer/document URL required')
        if (datetime.fromisoformat(date).date().isoformat()!=date or datetime.fromisoformat(period).date().isoformat()!=period
            or not datetime.fromisoformat(period)<datetime.fromisoformat(date)) or period[-5:] not in {'03-31','06-30','09-30','12-31'}:raise ValueError('Report period/publication mismatch')
        key=(code,doc)
        if key in identities:raise ValueError('Repeated document identity')
        identities.add(key);groups.setdefault((code,period,date),[]).append(len(inputs))
        file=cap['file'];path=workspace/file
        if (Path(file).name!=file or path.is_symlink() or not path.is_file() or cap.get('status')!=200 or cap.get('valid') is not True
            or not registered<=clock(cap['requested_at'])<=clock(cap['observed_at'])<=finished
            or datetime.fromisoformat(date).replace(tzinfo=timezone(timedelta(hours=8)))>clock(cap['observed_at'])):raise ValueError('Invalid file, status or observed clock')
        with path.open('rb') as f:body=f.read(10*1024**2+1)
        used+=len(body)
        if len(body)>10*1024**2 or used>24*1024**2 or not body.startswith(b'%PDF-') or len(body)!=cap['bytes'] or sha(body)!=cap['sha256']:raise ValueError('Retained PDF bytes or budget changed')
        inputs.append((path,body,cap))
    if any(len(ids)<2 for ids in groups.values()):raise ValueError('Each declared group needs at least two same-day candidates')
    deadline=time.monotonic()+60;budget={'pages':0,'text':0};documents=[]
    for path,body,cap in inputs:documents.append({'symbol':cap['symbol'],'document_id':cap['document_id'],'exact_URL':cap['URL'],'source_sha256':cap['sha256'],**inspect_pdf(body,deadline,budget)})
    comparisons=[]
    for (code,period,date),ids in groups.items():
        docs=[documents[i] for i in ids]
        def pixels(d):return [(x['pixel_width'],x['pixel_height'],x['RGB72_with_annotations_sha256']) for x in d['pages']]
        comparisons.append({'symbol':code,'period':period,'published_date':date,'document_ids':[d['document_id'] for d in docs],
            'PDF_bytes_equal':len({d['source_sha256'] for d in docs})==1,'page_counts_equal':len({d['page_count'] for d in docs})==1,
            'exact_extracted_text_equal':len({d['text_sha256'] for d in docs})==1,
            'whitespace_removed_text_equal':len({d['whitespace_removed_text_sha256'] for d in docs})==1,
            'all_RGB72_pages_equal':all(pixels(d)==pixels(docs[0]) for d in docs[1:]),
            'any_summary_marker_on_cover':any(d['first_page_summary_marker'] for d in docs),
            'manual_content_review_required':True,'automatic_canonical_selection':False,'revision_order_inferred':False})
    if manifest_file.read_bytes()!=raw or any(path.read_bytes()!=body for path,body,_ in inputs) or sha(Path(__file__).read_bytes())!=source_sha:raise ValueError('Inputs or inspector changed during review')
    report={'schema':VERSION,'manifest_sha256':sha(raw),'inspector_sha256':source_sha,'PyMuPDF_version':fitz.VersionBind,'limits':dict(LIMITS),
            'documents':documents,'comparisons':comparisons,'financial_values_generated':False,'whitespace_comparison_is_not_value_or_document_equivalence':True,
            'hard_RSS_or_OS_sandbox_certified':False,'issuer_attestation_verified':False,'new_data_or_strategy_validation':False,'promotion_allowed':False,'commercial_ready':False}
    report['report_digest']=sha(json.dumps(report,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
    return report


def publish(report,output):
    if not output.parent.is_dir():raise ValueError('Existing output parent required')
    encoded=json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2).encode()
    if len(encoded)>2*1024**2:raise ValueError('Diagnostic output budget exhausted')
    staged=None
    try:
        with tempfile.NamedTemporaryFile('wb',prefix='.financial-doc-',dir=output.parent,delete=False) as f:
            staged=Path(f.name);f.write(encoded);f.flush();os.fsync(f.fileno())
        os.link(staged,output)
    finally:
        if staged is not None:staged.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('New output required')
    publish(review(a.workspace),a.output)


if __name__=='__main__':main()
