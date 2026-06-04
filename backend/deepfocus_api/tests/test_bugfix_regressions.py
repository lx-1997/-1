"""体验报告缺陷/优化的回归测试，锁定关键纯函数逻辑，防止后续重构回退。

覆盖：
- profit_factor 共享 helper（无亏损封顶、正常、全零、abs）
- backtest 指标钳制与短样本保护
- 雪球降级判别正则（覆盖连接层失败 + 不误判价格文本）
- 入库垃圾文件守卫
"""

import pytest
from fastapi import HTTPException

from deepfocus_api.shared_utils import profit_factor
from deepfocus_api.backtest_engine import calculate_backtest_metrics, _clamp_metric
from deepfocus_api.data_sources import _xueqiu_access_blocked
from deepfocus_api.main import _reject_non_ingestible_file


# ---------- profit_factor（修复项 #4 / #13：无亏损不再返回误导性的 0）----------

def test_profit_factor_caps_when_no_losses_but_has_wins():
    assert profit_factor(300, 0) == 999.99


def test_profit_factor_zero_when_no_trades():
    assert profit_factor(0, 0) == 0.0


def test_profit_factor_normal_ratio():
    assert profit_factor(300, 100) == 3.0


def test_profit_factor_takes_abs_of_losses():
    # 调用方可能传有符号亏损
    assert profit_factor(300, -100) == 3.0


def test_profit_factor_caps_extreme_ratio():
    assert profit_factor(1_000_000_000, 1) == 999.99


# ---------- 指标钳制（修复项 #8：短样本/极端值失真）----------

def test_clamp_metric_bounds():
    assert _clamp_metric(5, 0, 10) == 5
    assert _clamp_metric(20, 0, 10) == 10
    assert _clamp_metric(-5, 0, 10) == 0


def test_clamp_metric_handles_nan_and_inf():
    assert _clamp_metric(float("inf"), 0, 10) == 0.0
    assert _clamp_metric(float("-inf"), 0, 10) == 0.0
    assert _clamp_metric(float("nan"), 0, 10) == 0.0


def test_backtest_metrics_clamps_short_sample_blowup():
    # 3 点曲线曾算出 annualized≈3.3e11%、sharpe≈36 等荒诞值
    metrics = calculate_backtest_metrics([100000, 100, 200000])
    assert metrics["annualized_return"] <= 10000.0
    assert -100.0 <= metrics["sharpe_ratio"] <= 100.0
    assert -10.0 <= metrics["beta"] <= 10.0


def test_backtest_metrics_too_short_returns_zeros():
    metrics = calculate_backtest_metrics([100000])
    assert metrics["annualized_return"] == 0
    assert metrics["profit_factor"] == 0


def test_backtest_metrics_no_loss_curve_caps_profit_factor():
    # 全程上涨（无亏损）曾返回 profit_factor=0；现应封顶
    metrics = calculate_backtest_metrics([100000, 101000, 102000, 103000])
    assert metrics["profit_factor"] == 999.99


# ---------- 雪球降级判别（修复项 #7 / #16）----------

def test_xueqiu_blocked_matches_connection_layer_failure():
    # httpx 异常分支产生的告警，无 "HTTP " 前缀
    assert _xueqiu_access_blocked(["雪球请求失败：403 Forbidden"]) is True


def test_xueqiu_blocked_matches_http_status_warning():
    assert _xueqiu_access_blocked(["雪球返回 HTTP 403，可能需要官方授权或遇到反爬。"]) is True


def test_xueqiu_blocked_matches_waf():
    assert _xueqiu_access_blocked(["疑似 WAF 拦截"]) is True


def test_xueqiu_blocked_ignores_benign_login_notice():
    # line 1085 的正常提示不得触发降级
    assert _xueqiu_access_blocked(["已使用配置的雪球登录态请求；请确保该账号和用途符合雪球规则。"]) is False


def test_xueqiu_blocked_does_not_false_positive_on_price_text():
    # 修复 #16：收紧后不再把 ¥403.5 之类价格误判为封禁
    assert _xueqiu_access_blocked(["当前价 ¥403.5，较前一日上涨"]) is False


# ---------- 入库垃圾文件守卫（修复项 #10 / #14）----------

@pytest.mark.parametrize("name", [".DS_Store", "Thumbs.db", ".hidden", "", "malware.exe", "archive.zip"])
def test_reject_non_ingestible_file_blocks_junk(name):
    with pytest.raises(HTTPException) as exc:
        _reject_non_ingestible_file(name)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("name", ["report.pdf", "data.txt", "财报.docx", "table.xlsx", "notes.md"])
def test_reject_non_ingestible_file_allows_documents(name):
    # 正常研报类型不应抛异常
    assert _reject_non_ingestible_file(name) is None
