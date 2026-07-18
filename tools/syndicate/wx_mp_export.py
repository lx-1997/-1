# -*- coding: utf-8 -*-
"""微信订阅号「每日复盘」导出 → 草稿箱(draft/add)。

订阅号是微信生态唯一不受认证墙限制的内容入口；本脚本把当日复盘渲染成图文 HTML + 封面，
推进**草稿箱**（不直接群发，留人工点发布，防误发）。配 WECHAT_APPID/WECHAT_SECRET 才真推；
否则 DRY_RUN：只在本地写出 body.html + cover.png 供预览。

用法：
    DEEPFOCUS_API_BASE=http://127.0.0.1:8300 python3 -m tools.syndicate.wx_mp_export            # 最新一期, DRY_RUN
    WECHAT_APPID=wx.. WECHAT_SECRET=.. python3 -m tools.syndicate.wx_mp_export --date 2026-06-26 --publish
注意：微信 API 需把服务器出口 IP 加进公众号「IP 白名单」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.syndicate import common  # noqa: E402

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={a}&secret={s}"
MATERIAL_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={t}&type=image"
DRAFT_URL = "https://api.weixin.qq.com/cgi-bin/draft/add?access_token={t}"


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _upload_thumb(token: str, png_path: str) -> str:
    """上传封面图为永久素材，返回 media_id（作 thumb_media_id）。"""
    boundary = "----dfsynboundary7f3a"
    with open(png_path, "rb") as f:
        data = f.read()
    pre = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; "
           f"filename=\"cover.png\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(MATERIAL_URL.format(t=token), data=pre + data + post,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if "media_id" not in res:
        raise SystemExit(f"封面上传失败：{res}")
    return res["media_id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD；缺省=最新一期")
    ap.add_argument("--out", default="./syndicate_out", help="DRY_RUN 输出目录")
    ap.add_argument("--publish", action="store_true", help="真推草稿箱（需 WECHAT_APPID/SECRET）")
    args = ap.parse_args()

    review = common.fetch_review(args.date)
    titles = common.title_candidates(review)
    title = titles[0]
    html = common.body_html_wechat(review)
    outdir = os.path.join(args.out, f"wx_{review.get('date')}")
    os.makedirs(outdir, exist_ok=True)
    cards = common.render_cards(review, outdir)
    cover = cards[0]
    with open(os.path.join(outdir, "body.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(outdir, "titles.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(titles))

    appid, secret = os.getenv("WECHAT_APPID", "").strip(), os.getenv("WECHAT_SECRET", "").strip()
    if not (args.publish and appid and secret):
        print(f"[DRY_RUN] 已写出：{outdir}/body.html, cover={cover}\n"
              f"          标题候选：{titles}\n"
              f"          配 WECHAT_APPID/WECHAT_SECRET + --publish 即推草稿箱（人工再点发布）。")
        return 0

    tok = _post_json_get(TOKEN_URL.format(a=appid, s=secret))
    token = tok.get("access_token")
    if not token:
        raise SystemExit(f"取 access_token 失败：{tok}（检查 IP 白名单/appsecret）")
    thumb = _upload_thumb(token, cover)
    draft = {"articles": [{
        "title": title[:64],
        "author": "DeepFocus",
        "digest": (common._ctx(review)["one"] or title)[:120],
        "content": html,
        "thumb_media_id": thumb,
        "content_source_url": common.backlink(review),
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }]}
    res = _post_json(DRAFT_URL.format(t=token), draft)
    if res.get("media_id"):
        print(f"[OK] 已推草稿箱 media_id={res['media_id']}。去公众号后台「草稿箱」核对后人工群发。")
        return 0
    raise SystemExit(f"draft/add 失败：{res}")


def _post_json_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "DeepFocusSyndicate/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
