"""收款/购买配置：套餐价格 + 收款码图片。个人收款码方案——支付与开通解耦：
用户扫码付款 → 私信管理员（带用户名）→ 管理员看板核对后开会员/发码。

存储：配置（价格/说明/启用）落 JSON 文件；收款码图片落 payment_qr/ 目录、经 /api/payment-qr/{which} 提供。
管理端（看板）可改价、上传收款码，零代码改动。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("deepfocus.payment")

_DIR = Path(__file__).resolve().parents[1]
_CFG_PATH = _DIR / ".payment_config.json"
_QR_DIR = _DIR / "payment_qr"
_PROVIDERS = ("wechat", "alipay")
_ALLOWED_QR = _PROVIDERS  # 兼容旧引用


def _valid_qr_key(which: str) -> Optional[str]:
    """校验/归一收款码 key：
    - 通用码：`wechat` / `alipay`
    - 每套餐固定金额码：`<provider>_<pkgkey>`，如 `alipay_month`（pkgkey 仅限字母数字下划线）
    返回归一后的安全 key（仅 [a-z0-9_]），非法返回 None。"""
    w = (which or "").strip().lower()
    if w in _PROVIDERS:
        return w
    for prov in _PROVIDERS:
        prefix = prov + "_"
        if w.startswith(prefix):
            sub = w[len(prefix):]
            if sub and all(c.isalnum() or c == "_" for c in sub) and len(sub) <= 24:
                return f"{prov}_{sub}"
    return None

_DEFAULT: dict[str, Any] = {
    "enabled": True,
    "note": "扫码支付对应金额，付款时请在备注里写上你的用户名。",
    # 自助兑换码店铺 URL（如闲鱼/淘宝/知识星球，付款后自动发卡密 → 用户回购买弹层「兑换会员码」秒开通）。
    # 空 = 不展示该入口；运营在看板填入后前端自动出现「想立刻开通？店铺自助秒发卡密 →」链接，无需改代码。
    "storefront_url": "",
    # orig = 原价（锚定价，用于「限时特惠」划线展示）；price = 现售价
    "packages": [
        {"key": "month", "label": "月卡", "days": 30, "price": 40, "orig": 68},
        {"key": "quarter", "label": "季卡", "days": 90, "price": 100, "orig": 198},
        {"key": "half", "label": "半年卡", "days": 180, "price": 170, "orig": 358},
        {"key": "year", "label": "年卡", "days": 365, "price": 280, "orig": 698},  # 折扣梯度须随时长递增：年卡必须是全场最低折(-60%)，否则"买长不如买短"
    ],
}


def get_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(_DEFAULT))  # 深拷贝默认
    try:
        if _CFG_PATH.exists():
            saved = json.loads(_CFG_PATH.read_text("utf-8"))
            if isinstance(saved, dict):
                for k in ("enabled", "note", "packages", "storefront_url"):
                    if k in saved:
                        cfg[k] = saved[k]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取收款配置失败：%s", exc)
    # 旧保存的套餐没有 orig（原价）→ 按 key 从默认补齐，保证「限时特惠」展示不缺
    _orig_by_key = {p["key"]: p.get("orig") for p in _DEFAULT["packages"]}
    for p in cfg.get("packages", []):
        if "orig" not in p and _orig_by_key.get(p.get("key")):
            p["orig"] = _orig_by_key[p["key"]]
    cfg["wechat"] = (_QR_DIR / "wechat.png").exists()
    cfg["alipay"] = (_QR_DIR / "alipay.png").exists()
    # 每套餐固定金额码：pkg_qr[pkgkey] = {wechat: bool, alipay: bool}（存在则前端扫码自动带金额）
    pkg_qr: dict[str, Any] = {}
    for p in cfg.get("packages", []):
        k = str(p.get("key") or "")
        if not k:
            continue
        pkg_qr[k] = {prov: (_QR_DIR / f"{prov}_{k}.png").exists() for prov in _PROVIDERS}
    cfg["pkg_qr"] = pkg_qr
    return cfg


def set_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    out = {
        "enabled": cfg["enabled"],
        "note": cfg["note"],
        "packages": cfg["packages"],
        "storefront_url": cfg.get("storefront_url", ""),
    }
    if "enabled" in updates:
        out["enabled"] = bool(updates["enabled"])
    if "note" in updates and isinstance(updates["note"], str):
        out["note"] = updates["note"][:300]
    if "storefront_url" in updates and isinstance(updates["storefront_url"], str):
        u = updates["storefront_url"].strip()[:300]
        # 只接受 http(s) 链接或清空；非法值忽略（防止把脏串渲染成链接）
        out["storefront_url"] = u if (u == "" or u.startswith("http://") or u.startswith("https://")) else out["storefront_url"]
    if "packages" in updates and isinstance(updates["packages"], list):
        clean = []
        for p in updates["packages"][:12]:
            try:
                row = {
                    "key": str(p.get("key") or "")[:16] or "pkg",
                    "label": str(p.get("label") or "")[:16] or "套餐",
                    "days": max(1, int(p.get("days") or 30)),
                    "price": max(0, int(float(p.get("price") or 0))),
                }
                orig = int(float(p.get("orig") or 0))
                if orig > row["price"]:  # 原价必须高于现价才有意义
                    row["orig"] = orig
                clean.append(row)
            except (TypeError, ValueError):
                continue
        if clean:
            out["packages"] = clean
    try:
        _CFG_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入收款配置失败：%s", exc)
    return get_config()


def save_qr(which: str, data: bytes) -> bool:
    key = _valid_qr_key(which)
    if key is None or not data:
        return False
    try:
        _QR_DIR.mkdir(exist_ok=True)
        (_QR_DIR / f"{key}.png").write_bytes(data)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存收款码失败：%s", exc)
        return False


def qr_file(which: str) -> Optional[Path]:
    key = _valid_qr_key(which)
    if key is None:
        return None
    p = _QR_DIR / f"{key}.png"
    return p if p.exists() else None
