"""Readable synthetic PDFs for CLI preflight tests; never production evidence."""
import copy,hashlib
import fitz


def readable(manifest,sources,*,cover='2025 年第一季度报告',pages=2):
    manifest=copy.deepcopy(manifest);sources=dict(sources)
    for row in manifest['vintages']:
        old=row['source_sha256'];name=manifest['source_files'].pop(old);sources.pop(old)
        with fitz.open() as doc:
            for i in range(pages):
                page=doc.new_page()
                if cover:page.insert_text((40,50),cover+'\nSYNTHETIC CLI TEST ONLY\n'+row['symbol']+' / '+row['document_id'],fontname='china-s',fontsize=12)
            raw=doc.tobytes()
        sha=hashlib.sha256(raw).hexdigest();sources[sha]=raw;manifest['source_files'][sha]=name;row['source_sha256']=sha
    return manifest,sources
