from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(ROOT / ".env")

from backend.deepfocus_api.data_sources import (  # noqa: E402
    _configured_single_line_value,
    _configured_xueqiu_cookie,
    _fetch_xueqiu_keyword,
    XUEQIU_REFERER_ENV,
    XUEQIU_USER_AGENT_ENV,
)


async def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "特斯拉"
    cookie = _configured_xueqiu_cookie()
    user_agent = _configured_single_line_value(XUEQIU_USER_AGENT_ENV)
    referer = _configured_single_line_value(XUEQIU_REFERER_ENV)
    items, warnings = await _fetch_xueqiu_keyword(
        keyword=keyword,
        symbol=None,
        limit=5,
        sort="time_desc",
        freshness="week",
    )
    blocked = any(re.search(r"WAF|验证码|反爬|HTTP 401|HTTP 403|HTTP 418|HTTP 429", warning, re.I) for warning in warnings)
    print(json.dumps(
        {
            "keyword": keyword,
            "cookie_configured": bool(cookie),
            "cookie_length": len(cookie or ""),
            "user_agent_configured": bool(user_agent),
            "referer_configured": bool(referer),
            "xueqiu_items": len(items),
            "blocked_or_verification": blocked,
            "warnings": warnings[:5],
            "titles": [item.get("title") for item in items[:3]],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
