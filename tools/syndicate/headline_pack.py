# -*- coding: utf-8 -*-
"""头条号 / 百家号 / 企鹅号 / 雪球「一稿多发」包导出。

这些内容平台收录快、权重高，是绕开 prod 拦 bytespider 的死路、进入头条系亿级内容池的捷径；
文末裸链 = 高质量外链反哺百度权重。本脚本不自动发布（平台反垃圾严），只产出**人工粘贴包**：
    titles.txt   5 个标题候选（挑一个）
    body.md      正文 markdown（末尾带回站链接 + 免责）
    card_1..4.png 4 张图文卡（可作头条/百家组图）
    backlink.txt 裸链（粘进文末）
    copy.txt     「标题+正文+裸链」合一，方便一键全选复制

用法：
    DEEPFOCUS_API_BASE=http://127.0.0.1:8300 python3 -m tools.syndicate.headline_pack [--date 2026-06-26] [--out ./syndicate_out]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.syndicate import common  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD；缺省=最新一期")
    ap.add_argument("--out", default="./syndicate_out", help="输出根目录")
    args = ap.parse_args()

    review = common.fetch_review(args.date)
    d = review.get("date")
    outdir = os.path.join(args.out, f"pack_{d}")
    os.makedirs(outdir, exist_ok=True)

    titles = common.title_candidates(review)
    md = common.body_markdown(review)
    link = common.backlink(review)
    cards = common.render_cards(review, outdir)

    with open(os.path.join(outdir, "titles.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(titles))
    with open(os.path.join(outdir, "body.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(outdir, "backlink.txt"), "w", encoding="utf-8") as f:
        f.write(link + "\n")
    with open(os.path.join(outdir, "copy.txt"), "w", encoding="utf-8") as f:
        f.write(titles[0] + "\n\n" + md)

    print(f"[OK] 一稿多发包已生成：{outdir}")
    print(f"     标题候选 {len(titles)} 个 / 正文 body.md / 图文卡 {len(cards)} 张 / 裸链 {link}")
    print("     头条号/百家号/企鹅号 后台新建图文 → 粘 copy.txt + 传 card_*.png → 发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
