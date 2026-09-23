#!/usr/bin/env python3
"""给镜像同步脚本 sync.mjs 打「美投圈交易明细」补丁（精确补丁，禁整文件覆盖）。

补丁内容：
  A1 记录「空明细」帖 + 回填参数（MEITOU_HANDAN_REFRESH_EMPTY / MEITOU_HANDAN_BACKFILL_PAGES，默认关闭）
  A2 命中旧帖时的翻页判定：授权正常且存在空明细旧帖时继续翻页补齐；同时给每条抓到的帖子打标记
     （detailsAuthorized 表示「已用授权身份抓过」→ 仍为空说明本来就无明细，不再反复翻页）
  A3 授权失效降级期间，不再把上次成功抓到的 tradingInfo 明细抹成空
  A4 公开快照 site-data-lite.json 里剔除内部标记字段（对外载荷保持原样）

用法（服务器上以 root 执行）：
    python3 patch-sync-handan.py            # dry-run，只做锚点断言
    python3 patch-sync-handan.py --apply    # 生成 .new → node --check → 备份 → 原子替换

先写临时文件并通过 node --check 才替换线上文件；任何一步失败都不动生产文件。
兼容 Python 3.6（服务器 alinux 自带版本）。
"""
import pathlib
import shutil
import subprocess
import sys
import time

TARGET = pathlib.Path("/var/www/meitou-build/sync.mjs")

PATCHES = [
    (
        "A1 空明细帖清单 + 回填参数",
        """  const knownIds = new Set((previous?.trades?.posts || []).map((post) => post.id));
  try {
""",
        """  const knownIds = new Set((previous?.trades?.posts || []).map((post) => post.id));
  // 明细回填：匿名降级期间抓不到 tradingInfo，恢复授权后按需翻页补齐（默认 0 = 关闭，只抓第一页）。
  // detailsAuthorized=true 表示「已用授权身份抓过仍为空」，这类帖子不再算缺失，避免每轮都翻页。
  const staleDetailIds = new Set((previous?.trades?.posts || [])
    .filter((post) => !(post?.transactions || []).length
      && !(post?.tradingPlan || []).length
      && !(post?.riskWarning || []).length
      && !post?.detailsAuthorized)
    .map((post) => post.id));
  const backfillBudget = Math.max(0, Number(process.env.MEITOU_HANDAN_REFRESH_EMPTY || 0));
  const backfillPageCap = Math.min(HANDAN_MAX_PAGES, Math.max(1, Number(process.env.MEITOU_HANDAN_BACKFILL_PAGES || 12)));
  let refreshedStale = 0;
  try {
""",
    ),
    (
        "A2 翻页判定 + 帖子授权标记",
        """      result.posts.push(...posts.map(normalizeHandanPost));
      if (posts.length && posts.every((post) => knownIds.has(post.id))) break;
""",
        """      const authorizedNow = !authExpired;
      const freshPosts = posts.map(normalizeHandanPost).filter(Boolean);
      for (const freshPost of freshPosts) {
        if (staleDetailIds.has(freshPost.id)) refreshedStale += 1;
        freshPost.detailsFetchedAt = Date.now();
        freshPost.detailsAuthorized = authorizedNow;
      }
      result.posts.push(...freshPosts);
      if (posts.length && posts.every((post) => knownIds.has(post.id))) {
        // 平时第一页全是旧帖就停；开了回填就继续翻页，直到补够预算或到页数上限
        const keepBackfilling = !authExpired && staleDetailIds.size > 0
          && refreshedStale < backfillBudget && page + 1 < backfillPageCap;
        if (!keepBackfilling) break;
      }
""",
    ),
    (
        "A3 降级期间保留上次的明细",
        """  if (!result.error) {
    // 增量命中旧帖提前停时 result.posts 只有一页；和上次快照按 id 合并，
""",
        """  if (!result.error && authExpired) {
    // 授权失效时匿名接口不带 tradingInfo，别把上次成功抓到的明细抹成空
    const previousById = new Map((previous?.trades?.posts || []).map((post) => [post.id, post]));
    result.posts = result.posts.map((post) => {
      const old = previousById.get(post.id);
      if (!old || !(old.transactions || []).length || (post.transactions || []).length) return post;
      return {
        ...post,
        transactions: old.transactions,
        riskLevel: old.riskLevel,
        riskWarning: old.riskWarning,
        tradingPlan: old.tradingPlan,
        tradingPhilosophy: old.tradingPhilosophy,
        previousPositions: old.previousPositions,
        detailsAuthorized: old.detailsAuthorized,
        detailsFetchedAt: old.detailsFetchedAt,
      };
    });
  }
  if (!result.error) {
    // 增量命中旧帖提前停时 result.posts 只有一页；和上次快照按 id 合并，
""",
    ),
    (
        "A4 公开快照剔除内部标记",
        """    posts: (payload.trades.posts || []).slice(0, 80),
""",
        """    posts: (payload.trades.posts || []).slice(0, 80)
      .map(({ detailsFetchedAt, detailsAuthorized, ...rest }) => rest),
""",
    ),
]


def node_check(path):
    proc = subprocess.Popen(["node", "--check", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    return proc.returncode, (err or out).decode("utf-8", "replace")


def main():
    apply = "--apply" in sys.argv
    if not TARGET.exists():
        print("找不到 %s" % TARGET)
        return 1
    text = TARGET.read_text()
    for name, old, new in PATCHES:
        count = text.count(old)
        print("[%s] %s: 锚点命中 %d 次" % ("OK " if count == 1 else "BAD", name, count))
        if count != 1:
            return 2
    if not apply:
        print("dry-run 通过（未写入）。加 --apply 生效。")
        return 0

    for name, old, new in PATCHES:
        text = text.replace(old, new, 1)

    # 必须保留 .mjs 后缀，否则 node --check 会按未知扩展名报错
    staging = TARGET.with_name(TARGET.stem + ".new.mjs")
    staging.write_text(text)
    code, output = node_check(staging)
    if code != 0:
        staging.unlink()
        print("新文件语法检查失败，未改动线上文件：%s" % output[:400])
        return 3

    backup = TARGET.with_name(TARGET.name + ".bak-handan-backfill-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(TARGET, backup)
    shutil.move(str(staging), str(TARGET))
    code, output = node_check(TARGET)
    if code != 0:
        shutil.copy2(backup, TARGET)
        print("替换后检查失败，已回滚：%s" % output[:400])
        return 4
    print("已写入 %s（备份 %s），node --check 通过" % (TARGET, backup))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
