"""写侧风控（write_guard）：滑窗限流 + junk 识别复用。"""
from __future__ import annotations

from deepfocus_api import write_guard as wg


def test_rate_limit_blocks_over_cap():
    actor = "rate-test-feedback"
    for _ in range(10):
        assert wg.check_rate("feedback", actor) is True
    # 第 11 次同窗口内被拦
    assert wg.check_rate("feedback", actor) is False
    # 不同 kind / 不同 actor 互不影响
    assert wg.check_rate("support", actor) is True
    assert wg.check_rate("feedback", actor + "-other") is True


def test_rate_limit_support_window():
    actor = "rate-test-support"
    for _ in range(5):
        assert wg.check_rate("support", actor) is True
    assert wg.check_rate("support", actor) is False


def test_junk_hit_reuses_news_filter():
    # 会议分享卡垃圾（news_filter 已拦的模式）在写侧同样命中
    assert wg.junk_hit("路透终端页面共享 #腾讯会议: 562-430-597") is not None
    # 正常反馈文本不误伤
    assert wg.junk_hit("这个答案的市盈率数据是错的") is None
    assert wg.junk_hit("") is None
    assert wg.junk_hit(None) is None  # type: ignore[arg-type]
