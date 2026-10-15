#!/usr/bin/env python3
"""Build the narrowly scoped DAO collector recovery patch; never restart services.

The collector lives outside this repository and contains private configuration.
Keep its snapshot private; only this transformation belongs in version control.
"""
import argparse
import ast
from pathlib import Path


OLD_PENDING = '''            # 处理延迟消息
            now_ts = time.time()
            if pending_all_queue:
                still_pending = []
                queue_changed = False
                for item in pending_all_queue:
                    content = str(item.get("content") or "").strip() if isinstance(item, dict) else ""
                    if not content:
                        queue_changed = True
                        continue
                    try:
                        send_at = float(item.get("send_at") or 0)
                    except Exception:
                        send_at = 0
                    if now_ts >= send_at:
                        send_time_all = time.strftime("%m-%d %H:%M:%S", time.localtime())
                        all_text = f"【内容】：{content}\\n【发送时间】：{send_time_all}"
                        print("开始向『所有消息群』发送延迟消息（无 AI）。")
                        if not send_to_dingtalk(all_text, DINGTALK_WEBHOOK_ALL, DINGTALK_SECRET_ALL):
                            item["send_at"] = now_ts + PENDING_ALL_RETRY_SECONDS
                            still_pending.append(item)
                            print(f"所有消息群发送失败，已延后 {PENDING_ALL_RETRY_SECONDS} 秒重试。")
                        queue_changed = True
                    else:
                        still_pending.append(item)
                pending_all_queue[:] = still_pending
                if queue_changed:
                    save_pending_all_queue(pending_all_queue)
'''

NEW_PENDING = '''            # 通知失败只影响通知；每轮最多一次，失败后全队列冷却。
            next_pending_all_attempt = _drain_pending_all_queue(
                pending_all_queue, next_pending_all_attempt,
            )
'''

HELPER = '''def _drain_pending_all_queue(queue: list, next_attempt_at: float) -> float:
    """Bound notification work so a failed downstream cannot starve ingestion."""
    now = time.time()
    if now < next_attempt_at:
        return next_attempt_at
    for index, item in enumerate(queue):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        try:
            send_at = float(item.get("send_at") or 0)
        except (TypeError, ValueError):
            send_at = 0
        if now < send_at:
            continue
        send_time_all = time.strftime("%m-%d %H:%M:%S", time.localtime())
        all_text = f"【内容】：{content}\\n【发送时间】：{send_time_all}"
        print("开始向『所有消息群』发送延迟消息（无 AI）。")
        delivered = send_to_dingtalk(all_text, DINGTALK_WEBHOOK_ALL, DINGTALK_SECRET_ALL)
        finished_at = time.time()
        if delivered:
            del queue[index]
        else:
            item["send_at"] = finished_at + PENDING_ALL_RETRY_SECONDS
            queue.append(queue.pop(index))
            print(f"所有消息群发送失败，全队列冷却 {PENDING_ALL_RETRY_SECONDS} 秒。")
        save_pending_all_queue(queue)
        return finished_at + (5.0 if delivered else PENDING_ALL_RETRY_SECONDS)
    return next_attempt_at


'''


def replace_once(source, old, new):
    count = source.count(old)
    if count != 1:
        raise ValueError("Expected exactly one patch anchor; found %s: %r" % (count, old[:75]))
    return source.replace(old, new, 1)


def make_candidate(source):
    source = replace_once(
        source,
        "token = get_api_token(force_reload=(attempt > 0))",
        "token = get_api_token(force_reload=True)",
    )
    source = replace_once(
        source,
        "        items = _normalize_futou_realinfo_clock(items)\n",
        "        # Preserve upstream timestamps; stale data must reach the freshness guard.\n",
    )
    source = replace_once(
        source,
        "    last_news_fetch = now_start                             # 快讯：立即开始轮询节奏\n",
        "    last_news_fetch = 0.0  # 首轮立即抓取，恢复采集不再等待一个完整周期。\n",
    )
    source = replace_once(
        source,
        "    pending_all_queue = _load_pending_all_state()\n",
        "    pending_all_queue = _load_pending_all_state()\n    next_pending_all_attempt = 0.0\n",
    )
    source = replace_once(source, OLD_PENDING, NEW_PENDING)
    source = replace_once(source, "def main(interval_seconds:", HELPER + "def main(interval_seconds:")
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("Use a separate candidate output; this tool never edits production in place.")
    candidate = make_candidate(args.source.read_text())
    args.output.write_text(candidate)
    args.output.chmod(0o600)
    print("Candidate compiled; six unique patch anchors matched.")


if __name__ == "__main__":
    main()
