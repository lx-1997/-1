"""用户交流群配置：群二维码 + 文案 + 客服兜底。面向【所有用户】(不登录/非会员也可见)的开放交流群。

为什么要一个独立可配置层而不是把二维码写死在前端：
**微信群二维码 7 天即失效**。写死在构建里 → 每周都要重新发版才能换码，且过期那几天用户扫了进不去。
所以走「活码」思路——二维码图片落 community_qr/ 目录、经 /api/community/qr/{which} 提供，运营每周在看板
换一张即可，零代码、零发版。再加一道客服兜底(个人名片码不失效)：群码过期/群满 200 人时，加客服备注「进群」拉人。

存储：文案/失效日期/客服号落 JSON；二维码图片落 community_qr/。完全对齐 payment_config 的成熟做法。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("deepfocus.community")

_DIR = Path(__file__).resolve().parents[1]
_CFG_PATH = _DIR / ".community_config.json"
_QR_DIR = _DIR / "community_qr"
# group = 每周更换的微信群二维码；cs = 客服个人名片码(不失效，兜底拉群)
_WHICH = ("group", "cs")

_DEFAULT: dict[str, Any] = {
    "enabled": True,
    "title": "DeepFocus 用户交流群",
    "subtitle": "免费 · 对所有用户开放（不用登录、不用会员）",
    # 群里干啥——给用户一个进群的理由，逐条短句
    "perks": [
        "🗣️ 和同样在看盘的人聊行情、唠个股",
        "⚡ 第一手快讯、每日 A 股收盘复盘，群里同步划重点",
        "🎁 不定期群友专属福利 / 限时会员口令",
    ],
    # 失效日期 YYYY-MM-DD：前端据此提示「X 月 X 日前有效」；留空则不显示失效提示
    "expires_at": "",
    # 客服微信号(兜底)：群码失效 / 群满时，加客服备注「进群」拉你进。留空则隐藏兜底入口
    "cs_wechat": "",
    # 免责红线：交流群是用户自由交流，绝不构成投资建议(合规)
    "disclaimer": "群内为用户自由交流，仅供参考、不构成任何投资建议，请独立判断、理性甄别。",
}

# 可被运营改写的文本字段(白名单)：长度上限防脏串
_TEXT_FIELDS = {
    "title": 40,
    "subtitle": 60,
    "expires_at": 16,
    "cs_wechat": 40,
    "disclaimer": 200,
}


def _valid_which(which: str) -> Optional[str]:
    w = (which or "").strip().lower()
    return w if w in _WHICH else None


def get_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(_DEFAULT))  # 深拷贝默认
    try:
        if _CFG_PATH.exists():
            saved = json.loads(_CFG_PATH.read_text("utf-8"))
            if isinstance(saved, dict):
                for k in ("enabled", "title", "subtitle", "perks", "expires_at", "cs_wechat", "disclaimer"):
                    if k in saved:
                        cfg[k] = saved[k]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取交流群配置失败：%s", exc)
    cfg["enabled"] = bool(cfg.get("enabled", True))
    # 二维码是否已上传(前端据此决定显示图 / 占位)
    cfg["group_qr"] = (_QR_DIR / "group.png").exists()
    cfg["cs_qr"] = (_QR_DIR / "cs.png").exists()
    return cfg


def set_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {
        "enabled": cfg["enabled"],
        "title": cfg["title"],
        "subtitle": cfg["subtitle"],
        "perks": cfg["perks"],
        "expires_at": cfg["expires_at"],
        "cs_wechat": cfg["cs_wechat"],
        "disclaimer": cfg["disclaimer"],
    }
    if "enabled" in updates:
        out["enabled"] = bool(updates["enabled"])
    for field, limit in _TEXT_FIELDS.items():
        if field in updates and isinstance(updates[field], str):
            out[field] = updates[field].strip()[:limit]
    if "perks" in updates and isinstance(updates["perks"], list):
        clean = [str(x).strip()[:60] for x in updates["perks"][:6] if str(x).strip()]
        if clean:
            out["perks"] = clean
    try:
        _CFG_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入交流群配置失败：%s", exc)
    return get_config()


def save_qr(which: str, data: bytes) -> bool:
    key = _valid_which(which)
    if key is None or not data:
        return False
    try:
        _QR_DIR.mkdir(exist_ok=True)
        (_QR_DIR / f"{key}.png").write_bytes(data)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存交流群二维码失败：%s", exc)
        return False


def qr_file(which: str) -> Optional[Path]:
    key = _valid_which(which)
    if key is None:
        return None
    p = _QR_DIR / f"{key}.png"
    return p if p.exists() else None
