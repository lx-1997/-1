#!/usr/bin/env python3
"""DeepFocus 生产后端精确补丁器(本会话验证过的固定解法)。

用法(在服务器上,用生产 venv 跑):
    /opt/deepfocus/venv/bin/python3.11 precise_patch.py \
        --file /opt/deepfocus/backend/deepfocus_api/main.py \
        --old  /tmp/old_block.txt \
        --new  /tmp/new_block.txt

约定:把"修改前的精确代码块"写进 --old 文件,"修改后"写进 --new 文件(用文件而非命令行
参数,彻底避开 shell 引号地狱;old 块通常用 awk 从 Mac 与服务器各提取一份并 diff 确认逐字一致)。

安全保证:① old 块在目标文件中必须**恰好出现一次**(否则 abort,不动文件);② 若 new 已存在
则判为已打过补丁直接退出(幂等);③ 改前自动备份 .bak-<时间戳>;④ 改后 py_compile 验证语法。

注意:py_compile 只查语法,**挡不住 lifespan 运行期崩溃**——补丁若碰了导入链/启动逻辑,
重启后务必轮询 /health 并冒烟验证(见 SKILL.md 流程 A 第 4 步)。
"""
import argparse
import shutil
import sys
import time
import py_compile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="目标文件(生效路径)")
    ap.add_argument("--old", required=True, help="含'修改前精确代码块'的文件")
    ap.add_argument("--new", required=True, help="含'修改后代码块'的文件")
    ap.add_argument("--no-compile", action="store_true", help="跳过 py_compile(非 .py 文件)")
    args = ap.parse_args()

    old = open(args.old, encoding="utf-8").read()
    new = open(args.new, encoding="utf-8").read()
    src = open(args.file, encoding="utf-8").read()

    if not old.strip():
        print("ABORT: old block 为空")
        return 2
    if new in src and old not in src:
        print("ALREADY_PATCHED — new 块已存在,无需改动")
        return 0
    n = src.count(old)
    if n != 1:
        print(f"ABORT: old 块在目标文件出现 {n} 次(必须恰好 1 次)。锚点不唯一/与服务器分叉,请缩小块或核对。")
        return 2

    bak = f"{args.file}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.file, bak)
    print("backup ->", bak)

    patched = src.replace(old, new, 1)
    assert patched.count(new) >= 1, "new 块替换后未出现,异常"
    open(args.file, "w", encoding="utf-8").write(patched)

    if not args.no_compile and args.file.endswith(".py"):
        py_compile.compile(args.file, doraise=True)
        print("OK: 已替换 + py_compile 通过")
    else:
        print("OK: 已替换(跳过 py_compile)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
