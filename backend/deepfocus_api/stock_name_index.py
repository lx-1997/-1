"""A 股「名称→代码」索引：用于判断一条提问是否锁定了某只**具体个股**。

背景：全市场扫描类技能(股东增减持/A股财报/重大事项)产出的是「全市场清单」。若用户问的是
某一只具体股票，却因句子里同时出现"A股"+事件词被当成全市场扫描，就会甩回一整张清单 = 答非所问。
带 6 位代码的好办(正则即可)，但用**中文名**锚定的(如"A股的宁德时代有没有回购")需要一份名称表才识别得出。

设计要点：
- 名称表后台拉取(东财全 A 列表，trust_env=False 绕沙箱代理)，落盘按日刷新；**检测走纯内存集合、零网络、同步**，
  契合 detector 在请求路径里同步调用的约束；拉取失败静默退回磁盘缓存 / 内置种子，绝不抛错阻塞路由。
- 只认 **≥3 字**的名称(避开"中国/招商"等 2 字名与常用词相撞)，宁可漏判极少数短名，不误伤合法全市场扫描；
  误判方向也安全：把"全市场扫描"降级成"个股研究"远好过把"个股提问"答成"全市场清单"。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import httpx

_CACHE_FILE = Path(
    os.getenv(
        "DEEPFOCUS_STOCK_NAME_CACHE",
        str(Path(__file__).resolve().parents[1] / ".stock_names.json"),
    )
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_MIN_LEN = 3   # 只认 ≥3 字的名称
_MAX_LEN = 8   # 名称扫描窗口上限(A 股名一般 ≤6 字，留点冗余)

# A 股 6 位代码：前后都不能再接数字/点，避免把 8 位公告号、日期串、"前20/近10天"误判为代码。
_SPECIFIC_CODE_RE = re.compile(r"(?<![\d.])\d{6}(?![\d.])")

# 冷启动 / 离线兜底种子：高频被问的大票，即使东财拉取失败也能挡住最常见的误触发。
_SEED_NAMES = frozenset({
    "宁德时代", "贵州茅台", "比亚迪", "隆基绿能", "中国平安", "招商银行", "五粮液", "美的集团",
    "中国移动", "工业富联", "长江电力", "药明康德", "东方财富", "海康威视", "紫金矿业", "中信证券",
    "京东方A", "中国神华", "山西汾酒", "恒瑞医药", "北方华创", "立讯精密", "中际旭创", "寒武纪",
    "金盘科技", "思源电气", "特变电工", "中国西电", "伊戈尔", "明阳电气", "华明装备", "阳光电源",
    "三一重工", "万科A", "格力电器", "中国石油", "中国石化", "中国建筑", "中芯国际", "韦尔股份",
    "通威股份", "片仔癀", "海天味业", "中国中免", "兆易创新", "汇川技术", "迈瑞医疗", "顺丰控股",
})
# ≥3 字但属常用词、可能误命中的歧义名(先留空，发现一个补一个)。
_DENY: frozenset = frozenset()

_lock = threading.Lock()
_names: frozenset = _SEED_NAMES
_maxlen = _MAX_LEN


def _ingest(mapping: dict) -> int:
    """把 name->code 映射并入内存集合(与种子合并、过滤短名/歧义名)。返回最终名称数。"""
    global _names, _maxlen
    cleaned = {n for n in mapping if n and len(n) >= _MIN_LEN and n not in _DENY}
    merged = frozenset(cleaned | _SEED_NAMES)
    with _lock:
        _names = merged
        _maxlen = min(_MAX_LEN, max((len(n) for n in merged), default=_MAX_LEN))
    return len(merged)


def _load_disk() -> bool:
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            _ingest(data)
            return True
    except Exception:
        pass
    return False


def _save_disk(mapping: dict) -> None:
    try:
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_FILE)
    except Exception:
        pass


async def _fetch_eastmoney() -> dict:
    """东财全 A 列表(沪/深主板+创业板+科创板+北交所)。直连绕代理，返回 name->code。

    东财 clist 每页实际上限 100 行(传更大的 pz 也只回 100)，故按返回的 total 翻页，
    收满或翻到空即停；上限 80 页(8000 只)兜底，防异常时死循环。
    """
    out: dict[str, str] = {}
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    total = None
    async with httpx.AsyncClient(trust_env=False, timeout=15.0, headers=_HEADERS) as client:
        for pn in range(1, 81):
            params = {
                "pn": str(pn), "pz": "100", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f12", "fs": fs, "fields": "f12,f14",
            }
            resp = await client.get(url, params=params)
            data = (resp.json() or {}).get("data") or {}
            if total is None:
                total = int(data.get("total") or 0)
            diff = data.get("diff") or []
            if not diff:
                break
            for item in diff:
                code = str(item.get("f12") or "").strip()
                name = str(item.get("f14") or "").strip()
                if code and name:
                    out[name] = code
            if total and len(out) >= total:
                break
    return out


async def refresh() -> int:
    """后台刷新名称表：拉东财→落盘→并入内存；失败退回磁盘缓存/种子。返回当前名称数。"""
    try:
        mapping = await _fetch_eastmoney()
    except Exception:
        mapping = {}
    if mapping:
        _save_disk(mapping)
        return _ingest(mapping)
    _load_disk()
    return len(_names)


def mentions_specific_code(text: str) -> bool:
    """文本是否含 6 位 A 股代码(只看代码，不查名称)。"""
    return bool(_SPECIFIC_CODE_RE.search(text or ""))


def mentions_specific_stock(text: str) -> bool:
    """文本是否锁定了某只**具体个股**——含 6 位代码，或命中任一已知 A 股名称(≥3 字)。

    命中 ⇒ 用户意图是「这只股」，全市场扫描类技能应让位给个股研究路径。
    检测为纯内存子串扫描(对仅几十字的提问开销可忽略)，无网络、可在同步 detector 中安全调用。
    """
    t = text or ""
    if _SPECIFIC_CODE_RE.search(t):
        return True
    names = _names
    if not names:
        return False
    n = len(t)
    mx = _maxlen
    for i in range(n):
        for length in range(_MIN_LEN, mx + 1):
            if i + length <= n and t[i:i + length] in names:
                return True
    return False


# 进程启动即尝试加载磁盘缓存(无网络、瞬时)；东财拉新由 lifespan 的 run_stock_name_refresh 触发。
_load_disk()
