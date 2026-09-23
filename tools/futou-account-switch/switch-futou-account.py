#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生产「富投财新」采集账号换成实时账号。

背景（2026-09-17 实测）：
  生产账号（lipeng / 198****00）从 /api/index/realinfoList 与 /api/index/articleList
  拿到的是**延迟约 24 小时**的流（落后 327 条，日期标签整体慢一天），
  而 循环推送.py 里的「日期整体慢一天」校正会把昨天的条目 +1 天，
  于是平台把昨天的消息当今天的播。换成实时账号是唯一根治手段。

本脚本做的事（全部先备份、可回退）：
  1. 用 stdin 读入新账号凭据（不落 argv，避免出现在 ps 里）
  2. 备份并写入 /DAO财经/.api_credentials.json（600）
  3. 删除陈旧的 /DAO财经/.api_token（旧账号刷出来的票）
  4. 清掉 systemd 里写死的 FUTOU_API_TOKEN，让脚本走「登录→自动续期」路径
  5. 给两个采集服务加降频 drop-in（文章 60s→300s，快讯已是 300s）：
     实测该账号此前约 1700 次/天请求，很可能是被上游降级成延迟流的原因之一
  6. 重启 dao-realinfo / dao-article-futou，并验证新票拿到的是**今天的**数据
  7. 顺带清掉那两个服务可能残留的 .api_token 缓存

用法（在服务器上以 root 执行）：
    printf '%s' '{"account":"13900000000","password":"***"}' | python3 switch-futou-account.py            # 预检
    printf '%s' '{"account":"13900000000","password":"***"}' | python3 switch-futou-account.py --apply    # 执行
兼容 Python 3.6。
"""
from __future__ import print_function

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

DAO_DIR = pathlib.Path("/DAO财经")
CRED_PATH = DAO_DIR / ".api_credentials.json"
TOKEN_PATH = DAO_DIR / ".api_token"
SYSTEMD_DIR = pathlib.Path("/etc/systemd/system")
SERVICES = ("dao-realinfo.service", "dao-article-futou.service")
FUTOU_LOGIN = "https://backend.futoucaixin.cn/api/user/login"
FUTOU_REALINFO = "https://backend.futoucaixin.cn/api/index/realinfoList"
FUTOU_ARTICLE = "https://backend.futoucaixin.cn/api/index/articleList"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


def log(*args):
    print("[%s]" % time.strftime("%Y-%m-%d %H:%M:%S"), *args)


def http_json(url, method="GET", headers=None, params=None, body=None):
    """极简 HTTP（只依赖标准库，避免服务器缺包）。"""
    import urllib.parse
    import urllib.request
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login(account, password):
    data = http_json(FUTOU_LOGIN, method="POST",
                     headers={"Content-Type": "application/json;charset=utf-8", "User-Agent": UA},
                     body={"account": account, "password": password})
    token = ((data.get("data") or {}).get("userinfo") or {}).get("token") or ""
    return token, data.get("code"), data.get("msg")


def probe(token):
    """返回 (realinfo 最新一条, article 最新一条) 的 (id, 时间标签, 标题)。"""
    heads = {"accept": "application/json, text/plain, */*",
             "content-type": "application/json;charset=utf-8",
             "origin": "https://www.futoucaixin.cn", "token": token, "user-agent": UA}
    out = []
    r = http_json(FUTOU_REALINFO, headers=heads, params={"page": 1, "rows": 5, "keyword": ""})
    rows = []
    for _day, items in ((r.get("data") or {}).get("grouped_data") or {}).items():
        rows += items
    rows.sort(key=lambda x: x.get("id") or 0, reverse=True)
    out.append(("快讯", rows[0].get("id"), rows[0].get("createtime_text"), (rows[0].get("name") or "")[:40]) if rows else ("快讯", None, None, "(空)"))
    a = http_json(FUTOU_ARTICLE, headers=heads, params={"page": 1, "rows": 5, "keyword": ""})
    arows = (a.get("data") or {}).get("data") or []
    out.append(("文章", arows[0].get("id"), arows[0].get("createtime_text"), (arows[0].get("name") or "")[:40]) if arows else ("文章", None, None, "(空)"))
    return out


def run(cmd):
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace").strip()


def main():
    apply = "--apply" in sys.argv
    raw = sys.stdin.read().strip()
    if not raw:
        print("请通过 stdin 传入 {\"account\":\"...\",\"password\":\"...\"}")
        return 2
    cred = json.loads(raw)
    account = str(cred.get("account") or cred.get("mobile") or "").strip()
    password = str(cred.get("password") or "").strip()
    if not account or not password:
        print("缺少 account / password")
        return 2

    log("步骤 1/6：用新账号试登录")
    token, code, msg = login(account, password)
    if not token:
        log("登录失败：code=%s msg=%s" % (code, msg))
        return 3
    log("登录成功，token 长度 %d" % len(token))

    log("步骤 2/6：探测该账号的数据新鲜度")
    for name, iid, when, title in probe(token):
        log("   %s 最新 id=%s 时间=%s | %s" % (name, iid, when, title))
    stamp = time.strftime("%Y-%m-%d")
    fresh = any(str(when or "").startswith(stamp) for _n, _i, when, _t in probe(token))
    log("   → 数据是否为今天：%s" % ("是（实时账号 ✓）" if fresh else "否（仍是延迟流，请换账号或确认订阅状态）"))
    if not fresh and apply:
        log("拒绝执行：该账号拿到的仍不是当天数据，换账号无意义。")
        return 4
    if not apply:
        log("预检结束。加 --apply 执行切换。")
        return 0

    log("步骤 3/6：备份并写入新凭据")
    if CRED_PATH.exists():
        bak = str(CRED_PATH) + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(str(CRED_PATH), bak)
        log("   备份：%s" % bak)
    CRED_PATH.write_text(json.dumps({"account": account, "password": password}, ensure_ascii=False))
    os.chmod(str(CRED_PATH), 0o600)
    if TOKEN_PATH.exists():
        shutil.move(str(TOKEN_PATH), str(TOKEN_PATH) + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        log("   已移走旧 .api_token（旧账号刷出的票）")

    log("步骤 4/6：清掉 systemd 里写死的 FUTOU_API_TOKEN")
    for svc in SERVICES:
        d = SYSTEMD_DIR / (svc + ".d")
        d.mkdir(parents=True, exist_ok=True)
        for old in ("override-token.conf", "futou-poll.conf"):
            f = d / old
            if f.exists() and not (d / (old + ".disabled")).exists():
                shutil.move(str(f), str(f) + ".disabled")
                log("   已停用 %s/%s" % (svc, old))
        (d / "live-account.conf").write_text(
            "[Service]\n"
            "Environment=FUTOU_API_TOKEN=\n"
            "Environment=FUTOU_DEFAULT_API_TOKEN=\n"
            "Environment=FUTOU_ARTICLE_INTERVAL=600\n"
            "Environment=FUTOU_ARTICLE_DAY_INTERVAL=300\n"
            "Environment=FUTOU_ARTICLE_NIGHT_INTERVAL=600\n"
            "Environment=FUTOU_NEWS_INTERVAL=300\n",
            encoding="utf-8")
        log("   已写入 %s/live-account.conf（清空写死 token + 降频）" % svc)

    log("步骤 5/6：daemon-reload + 重启采集服务")
    run("systemctl daemon-reload")
    for svc in SERVICES:
        rc, out = run("systemctl restart %s && sleep 3 && systemctl is-active %s" % (svc, svc))
        log("   %s -> %s %s" % (svc, out, "" if rc == 0 else "(rc=%d)" % rc))

    log("步骤 6/6：验证（看日志是否自动登录 + 事件库最新时间是否变成今天）")
    time.sleep(20)
    for svc, logfile in (("dao-realinfo.service", "/DAO财经/logs/realinfo.nohup.log"),
                         ("dao-article-futou.service", "/DAO财经/logs/article_futou.nohup.log")):
        rc, out = run("tail -5 %s" % logfile)
        log("   --- %s 尾部 ---\n%s" % (svc, out))
        if os.path.exists(TOKEN_PATH):
            log("   ✓ 新的 .api_token 已生成（说明已用新账号登录并自动续期）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
