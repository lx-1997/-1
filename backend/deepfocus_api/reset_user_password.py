"""管理员重置某用户登录口令（按 用户名 / 邮箱 / 手机号 定位）。

为什么是「重置」而不是「查询」：口令以 bcrypt 单向哈希存储，原文不可逆——
数据库里只有 `$2b$12$...` 这种哈希，设计上无法还原成明文。能查出明文反而是安全漏洞。
所以正路是给用户重置一个新口令（再让其自行登录后修改）。

在生产服务器 backend 目录(已 source 进 venv、与线上同环境变量)运行：
    # 自动生成一个强随机口令并打印（推荐：你不必想口令）
    python -m deepfocus_api.reset_user_password 18860122019

    # 指定新口令
    python -m deepfocus_api.reset_user_password 18860122019 --password 'NewPass123'

定位支持手机号 / 用户名 / 邮箱（大小写不敏感）。改密后会轮换该用户会话，旧端被挤下线。
"""
from __future__ import annotations

import argparse
import secrets
import string
import sys

from .auth import reset_password


def _gen_password(length: int = 12) -> str:
    # 去掉易混字符，含大小写+数字，便于口头/文字转交
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重置某用户登录口令（按手机号/用户名/邮箱）")
    parser.add_argument("identifier", help="手机号 / 用户名 / 邮箱")
    parser.add_argument("--password", "-p", default=None, help="指定新口令（省略则自动生成强随机口令）")
    args = parser.parse_args(argv)

    new_pw = args.password or _gen_password()
    try:
        user = reset_password(args.identifier, new_pw)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if user is None:
        print(f"✗ 未找到用户：{args.identifier}（手机号/用户名/邮箱均未匹配）", file=sys.stderr)
        return 1

    print("✓ 已重置口令")
    print(f"  用户名 : {user.username}")
    print(f"  手机号 : {user.phone or '—'}")
    print(f"  邮箱   : {user.email or '—'}")
    print(f"  新口令 : {new_pw}")
    print("  （请通过安全渠道转交用户；其旧登录会话已失效，需用新口令重新登录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
