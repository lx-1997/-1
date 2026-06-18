"""iFinD 财务三表指标校准探针（在**有 refresh token 的服务器**上运行）。

用途：financial_statements 的 iFinD 源默认关闭，因为不同 iFinD 账号/版本的 basic_data 指标 ID 可能不同。
本脚本用候选指标 ID 实测拉一只票，并和**已验证的东财值**并排比对，直接告诉你哪些 ID 对、哪些要换，
最后打印可直接粘贴的 DEEPFOCUS_IFIND_FIN_INDICATORS / DEEPFOCUS_IFIND_STATEMENTS 环境变量。只读、不写任何东西。

用法（在服务器 venv 内）：
    DEEPFOCUS_IFIND_REFRESH_TOKEN=... python -m deepfocus_api.ifind_fin_probe 600519
    python -m deepfocus_api.ifind_fin_probe 000651 002594        # 可多只

校准步骤：
  1) 跑本脚本，看每个字段 iFinD 返回值 vs 东财参考值是否量级一致；
  2) 若某字段 iFinD 返回 None 或明显不对 → 去同花顺 iFinD 数据接口文档查正确指标 ID，
     填进 DEEPFOCUS_IFIND_FIN_INDICATORS（JSON，只需覆盖要改的键）；
  3) 全部对上后，设 DEEPFOCUS_IFIND_STATEMENTS=1 启用 iFinD 为首选源（东财仍兜底）。
"""
from __future__ import annotations

import asyncio
import json
import sys

from . import ifind_api
from .eastmoney_data import fetch_eastmoney_cashflow, fetch_eastmoney_earnings
from .financial_statements import _IFIND_FIN_DEFAULT
from .shared_utils import safe_float

# iFinD 字段 → 东财参考值（用已实测的东财数据做"真值"对照，量级一致即说明 iFinD 指标 ID 选对）
_REF_LABEL = {
    "ocf": "经营活动现金流量净额", "icf": "投资活动现金流量净额", "fcf_financing": "筹资活动现金流量净额",
    "capex": "购建固定/无形/长期资产支付现金", "revenue": "营业收入", "net_income": "归母净利润",
    "deduct_np": "扣非归母净利润", "gross_margin": "销售毛利率%", "roe": "加权ROE%",
}


async def _eastmoney_ref(code: str) -> dict:
    perf = await fetch_eastmoney_earnings(code, "A") or {}
    cf = await fetch_eastmoney_cashflow(code, "A") or {}
    eps = safe_float(perf.get("eps")); deps = safe_float(perf.get("deduct_eps"))
    # 东财扣非净利 ≈ 归母净利 × (扣非EPS/EPS)
    ni = safe_float(perf.get("net_income"))
    deduct_np = (ni * deps / eps) if (ni and eps and deps is not None) else None
    return {"ocf": safe_float(cf.get("ocf")), "icf": safe_float(cf.get("icf")),
            "fcf_financing": safe_float(cf.get("fcf_financing")), "capex": safe_float(cf.get("capex")),
            "revenue": safe_float(perf.get("revenue")), "net_income": ni, "deduct_np": deduct_np,
            "gross_margin": safe_float(perf.get("gross_margin")), "roe": safe_float(perf.get("roe"))}


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{f/1e8:.2f}亿" if abs(f) >= 1e7 else f"{f:.4g}"
    except (TypeError, ValueError):
        return str(v)


def probe(codes: list[str]) -> None:
    print("=" * 72)
    if not ifind_api.enabled():
        print("✗ iFinD 未配置：请设置 DEEPFOCUS_IFIND_REFRESH_TOKEN 后再跑本脚本。")
        return
    indi = dict(_IFIND_FIN_DEFAULT)
    print(f"iFinD 已启用。候选指标映射（{len(indi)} 项）：")
    for k, v in indi.items():
        print(f"   {k:14s} -> {v}")
    print("=" * 72)

    ok_fields: set[str] = set()
    for code in codes:
        ref = asyncio.run(_eastmoney_ref(code))
        res = ifind_api.basic_data(code, ",".join(indi.values()))
        row = (res.get("rows") or [{}])[0] if res.get("ok") else {}
        if not res.get("ok"):
            print(f"\n[{code}] iFinD basic_data 失败：{res.get('error')}")
            continue
        print(f"\n[{code}]  字段 | iFinD 返回 | 东财参考 | 判定")
        print("-" * 72)
        for key, ind in indi.items():
            iv = safe_float(row.get(ind))
            rv = ref.get(key)
            verdict = "?"
            if iv is None:
                verdict = "✗ iFinD 无值（换指标ID）"
            elif rv is None:
                verdict = "· 东财无参考"
            else:
                rel = abs(iv - rv) / (abs(rv) + 1e-9)
                verdict = "✓ 一致" if rel < 0.15 else ("△ 量级近(核对)" if rel < 0.6 else "✗ 不符（换ID）")
                if rel < 0.15:
                    ok_fields.add(key)
            print(f"  {key:14s}({_REF_LABEL.get(key,'')[:10]}) | {_fmt(row.get(ind)):>10} | {_fmt(rv):>10} | {verdict}")

    print("\n" + "=" * 72)
    print(f"校准结果：{len(ok_fields)}/{len(indi)} 字段对上 → {sorted(ok_fields)}")
    print("把对不上的字段在下面 JSON 里替换成正确的 iFinD 指标 ID，再设到环境变量：\n")
    print("  export DEEPFOCUS_IFIND_FIN_INDICATORS='" + json.dumps(indi, ensure_ascii=False) + "'")
    print("  export DEEPFOCUS_IFIND_STATEMENTS=1     # 全部对上后再开，iFinD 优先、东财仍兜底")
    print("=" * 72)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]
    probe(args or ["600519", "000651", "002594"])
