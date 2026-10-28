"""Process-wide round robin for an explicitly enabled model pool.
Rotate a single frozen candidate list once per completion; retries retain the
remaining order and each request's payload/model/credential stay paired.
"""
from __future__ import annotations
import hashlib
import threading
from collections import OrderedDict

_lock = threading.Lock()
_cursors: OrderedDict[tuple, int] = OrderedDict()

def balance_candidates(candidates: list[dict], enabled: bool) -> list[dict]:
    if not enabled or len(candidates) < 2:
        return list(candidates)
    identity = tuple((str(c.get('provider','')), str(c.get('model','')), str(c.get('base_url','')),
                      hashlib.sha256(str(c.get('api_key','')).encode()).hexdigest()) for c in candidates)
    with _lock:
        index = _cursors.pop(identity, 0)
        _cursors[identity] = (index+1) % len(candidates)
        while len(_cursors)>32:
            _cursors.popitem(last=False)
    return candidates[index:]+candidates[:index]
