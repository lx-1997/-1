"""Read user-facing prose from a bounded, possibly unfinished JSON document."""
from __future__ import annotations
import json
import re
from contextvars import ContextVar

REPORT_PROSE_DELTA = ContextVar('report_prose_delta', default=None)

async def complete_report_json(llm, prompt, **kwargs):
    sink = REPORT_PROSE_DELTA.get()
    if sink is not None and hasattr(llm, 'complete_json_streaming'):
        sink(None)  # A new model call replaces any failed partial document.
        return await llm.complete_json_streaming(prompt, on_delta=sink, **kwargs)
    return await llm.complete_json(prompt, **kwargs)

def streamed_prose(prefix: str) -> dict:
    text = prefix[:128000].lstrip()
    if not text.startswith('{'): return {}
    found = {}
    index = 0
    def string():
        nonlocal index
        start = index = index + 1
        while index < len(text):
            char = text[index]
            if char == '"':
                raw = text[start:index]; index += 1
                return json.loads('"' + raw + '"'), True
            if char == '\\':
                if index + 1 >= len(text): break
                if text[index + 1] == 'u':
                    if index + 6 > len(text): break
                    if not re.fullmatch('[0-9a-fA-F]{4}', text[index + 2:index + 6]): raise ValueError()
                    index += 6; continue
                if text[index + 1] not in '"\\/bfnrt': raise ValueError()
                index += 2; continue
            if ord(char) < 32: raise ValueError()
            index += 1
        # Do not fabricate incomplete escapes or expose a lone surrogate.
        value = json.loads('"' + text[start:index] + '"')
        return value.encode('utf-8', errors='ignore').decode('utf-8'), False
    def spaces():
        nonlocal index
        while index < len(text) and text[index].isspace(): index += 1
    def value(path=(), depth=0):
        nonlocal index
        if depth > 16: raise ValueError()
        spaces()
        if index >= len(text): return False
        char = text[index]
        if char == '"':
            result, complete = string()
            if (len(path) == 1 and path[0] in {'one_liner','plain_language_summary','executive_summary','summary','core_logic'}
                or len(path) == 3 and path[0] in {'sections','logic_lines'} and path[2] in {'title','summary','evidence','chain','impact','watch'}
                or len(path) == 4 and path[0] == 'sections' and path[2] in {'paragraphs','bullets'}):
                found[path] = result
            return complete
        if char in '{[':
            object_ = char == '{'; closing = '}' if object_ else ']'; index += 1; item = 0
            while True:
                spaces()
                if index >= len(text): return False
                if text[index] == closing: index += 1; return True
                if object_:
                    if text[index] != '"': raise ValueError()
                    key, complete = string()
                    if not complete: return False
                    spaces()
                    if index >= len(text): return False
                    if text[index] != ':': raise ValueError()
                    index += 1
                else: key = item
                if not value((*path,key),depth+1): return False
                spaces()
                if index >= len(text): return False
                if text[index] == closing: index += 1; return True
                if text[index] != ',': raise ValueError()
                index += 1; item += 1
        else:
            start = index
            while index < len(text) and text[index] not in ',}]': index += 1
            if index == len(text): return False
            json.loads(text[start:index]); return True
    try: value()
    except (ValueError, RecursionError): pass
    intro = found.get(('one_liner',),'')
    summary = next((found[(key,)] for key in ('plain_language_summary','executive_summary','summary','core_logic') if found.get((key,))), '')
    sections = []
    for group in ('sections','logic_lines'):
        for number in range(12):
            title = found.get((group,number,'title'),'')
            paragraphs = [v for p,v in found.items() if len(p)==4 and p[:2]==(group,number) and p[2]=='paragraphs' and v]
            if not paragraphs:
                paragraphs = [found[(group,number,key)] for key in ('summary','evidence','chain','impact','watch') if found.get((group,number,key))]
            if title or paragraphs: sections.append({'title':title,'paragraphs':paragraphs[:12]})
    result = {'intro':intro,'summary':summary,'sections':sections}
    return result if intro or summary or sections else {}
