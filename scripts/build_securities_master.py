#!/usr/bin/env python3
"""构建证券主数据：新浪 A 股列表全量拉取 + 内置热门港美股（内置表在 securities_master.BUILTIN_HK_US）。

用法（生产 venv）：
    /opt/deepfocus/venv/bin/python3.11 scripts/build_securities_master.py
幂等可重跑；完成后 securities 表为全量 A 股（~5400）+ 内置港美热点。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

sys.path.insert(0, "backend")

from deepfocus_api import securities_master  # noqa: E402

SINA_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodeData?page={page}&num=100&node=hs_a"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}


def market_of(code: str, sina_symbol: str) -> str:
    prefix = (sina_symbol or "")[:2].lower()
    if prefix == "sh":
        return "SH"
    if prefix == "sz":
        return "SZ"
    if prefix == "bj":
        return "BJ"
    return "CN"


def fetch_page(page: int) -> list[dict]:
    req = urllib.request.Request(SINA_URL.format(page=page), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", errors="replace").strip()
    if not text or text == "null":
        return []
    return json.loads(text)


def main() -> int:
    rows: list[tuple[str, str, str]] = []
    page = 1
    empty_streak = 0
    while page <= 80:  # ~5400 只 / 100 每页 = 54 页，80 页硬顶防死循环
        try:
            items = fetch_page(page)
        except Exception as exc:  # noqa: BLE001 — 单页失败重试一次
            time.sleep(2)
            try:
                items = fetch_page(page)
            except Exception:
                print(f"page {page} failed: {exc}", file=sys.stderr)
                break
        if not items:
            empty_streak += 1
            if empty_streak >= 2:
                break
            page += 1
            continue
        empty_streak = 0
        for it in items:
            code = str(it.get("code") or "").strip()
            name = str(it.get("name") or "").strip()
            if not re.fullmatch(r"\d{6}", code) or not name:
                continue
            rows.append((code, name, market_of(code, it.get("symbol") or "")))
        print(f"page {page}: +{len(items)}")
        page += 1
        time.sleep(0.3)

    builtin = [(s, n, "HK/US") for s, n in securities_master.BUILTIN_HK_US.items()]
    total = securities_master.upsert_securities(rows + builtin)
    aliases = securities_master.alias_map()
    print(f"upserted {total} rows (A-share {len(rows)} + builtin {len(builtin)}); aliases={len(aliases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
