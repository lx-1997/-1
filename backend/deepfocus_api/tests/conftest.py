"""测试夹具：保证每个测试前主线程有可用事件循环。

为什么需要：本仓多个模块在**模块级**构造 asyncio.Lock()/Semaphore()(导入期即 get_event_loop())。
当某个测试用 asyncio.run() 跑完后，主线程当前事件循环被关闭/置空(py3.9)；若随后另一个测试**首次**惰性
import 这些模块(如 tear_sheet 测试里 `from deepfocus_api import main`)，导入期会 get_event_loop() 崩溃，
表现为随测试顺序而变的 flaky 失败。这里用 autouse 夹具在每个测试前补一个干净的事件循环，从根上消除该隔离脆弱性。
"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:  # 当前线程无事件循环(被 asyncio.run 置空) → 补一个
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
