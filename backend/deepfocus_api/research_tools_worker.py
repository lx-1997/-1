"""One bounded analysis process, no API imports, no account data, no orders."""
from __future__ import annotations

from contextlib import redirect_stdout
from contextlib import contextmanager
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import time

TOOLS = {
    "performance": ("策略绩效报告", "strategy", "QuantStats", "quantstats", "research_analytics"),
    "portfolio": ("组合风险体检", "strategy", "Riskfolio-Lib", "riskfolio-lib", "research_analytics"),
    "factors": ("因子研究实验", "strategy", "Qlib / LightGBM", "pyqlib", "research_factors"),
    "financials": ("财务趋势与筛选", "company", "FinanceToolkit", "financetoolkit", "research_analytics"),
    "institutions": ("机构与内部人变化", "company", "EdgarTools / SEC", "edgartools", "research_assets"),
    "funds": ("基金持仓穿透", "assets", "持仓穿透计算", None, "research_assets"),
    "bonds": ("可转债条款筛选", "assets", "转股价值与条款计算", None, "research_assets"),
}


@contextmanager
def compute_lock():
    """Share one heavy calculation across API workers and blue/green instances."""
    path = "/tmp/df-research-compute-" + str(os.getuid()) + ".lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RuntimeError("研究计算繁忙，请稍后重试")
                time.sleep(0.2)
        yield
    finally:
        os.close(descriptor)


def configure_identity() -> None:
    config = Path(os.getenv("DEEPFOCUS_RESEARCH_CONFIG", "/opt/deepfocus/research-runtime-config.json"))
    if not os.getenv("DEEPFOCUS_SEC_IDENTITY") and config.is_file():
        value = json.loads(config.read_text()).get("sec_identity")
        if isinstance(value, str) and "@" in value and "\n" not in value:
            os.environ["DEEPFOCUS_SEC_IDENTITY"] = value


def catalog() -> dict:
    descriptions = {
        "performance": "以真实日收益或净值评估策略表现，比较基准并导出报告",
        "portfolio": "核对共同交易日，查看相关性、风险贡献和尾部风险",
        "factors": "基于 OHLCV 训练因子模型，按时间隔离验证与测试",
        "financials": "比较多季度财务趋势，显示同报告期的财务指标",
        "institutions": "查询官方 SEC 披露，或导入 13F / Form 4 文件核对变化",
        "funds": "穿透基金重仓，核对重合度与未披露持仓比例",
        "bonds": "按转股价值与溢价率筛选，同时核对强赎和到期条件",
    }
    tools = []
    for tool, (title, category, engine, distribution, _) in TOOLS.items():
        available, version, reason = True, None, ""
        if distribution:
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                available = tool == "institutions"  # Standard-library official file import remains usable.
                reason = "分析运行环境尚未安装 " + engine
        if tool == "institutions" and not (os.getenv("DEEPFOCUS_SEC_IDENTITY") or os.getenv("SEC_USER_AGENT")):
            reason = "官方文件导入可用；在线查询需要配置 SEC 联系身份"
        tools.append(dict(tool=tool, title=title, category=category, engine=engine, description=descriptions[tool],
                          available=available, reason=reason, version=version))
    return {"tools": tools, "expires_in_minutes": 30}


def main() -> None:
    output = sys.stdout
    try:
        configure_identity()
        payload = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            if payload.get("operation") == "catalog":
                result = catalog()
            else:
                tool = payload.get("tool")
                if tool not in TOOLS:
                    raise ValueError("未知研究工具")
                data = payload.get("input")
                if not isinstance(data, dict):
                    raise ValueError("输入必须是 JSON 对象")
                module_name = TOOLS[tool][4]
                with compute_lock():
                    snapshot = None
                    if tool == "financials" and data.get("symbols"):
                        if data.get("records") or data.get("content_base64") or data.get("file_name"):
                            raise ValueError("SEC 财务查询不能与导入文件混用")
                        symbols = data["symbols"]
                        if isinstance(symbols, str):
                            symbols = [s for s in re.split(r"[,;，\s]+", symbols.strip()) if s]
                        from deepfocus_api.research_assets import fetch_sec_financial_records
                        snapshot = fetch_sec_financial_records(symbols, max_periods=8)
                        # Pass provenance through the domain report builder so
                        # repeated technical warnings can be summarized once.
                        data = {**snapshot, "period_type": "quarterly"}
                    module = __import__("deepfocus_api." + module_name, fromlist=["analyze_" + tool])
                    result = getattr(module, "analyze_" + tool)(data)
                    if snapshot:
                        result["sources"] = snapshot["sources"]
                    filename = str(payload.get("input", {}).get("file_name", ""))
                    if "虚构" in filename or "demo" in filename.lower():
                        result.setdefault("warnings", []).insert(0, "当前输入标记为演示数据，仅用于功能验证，不代表真实市场表现。")
        response = {"ok": True, "result": result}
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 4 * 1024 * 1024:
            raise ValueError("分析报告过大，请缩小范围")
    except Exception as error:
        status = 422 if isinstance(error, (ValueError, TypeError, KeyError)) else 503
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else "分析运行环境异常，请联系管理员"
        message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[联系身份]", message)[:500]
        encoded = json.dumps({"ok": False, "status": status, "error": message}, ensure_ascii=False)
    output.write(encoded)


if __name__ == "__main__":
    main()
