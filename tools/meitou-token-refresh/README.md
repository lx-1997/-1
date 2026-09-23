# 美投镜像 Bearer token 自动续期（Mac 端）

镜像站 <https://daocaijing.com/meitou/> 的会员内容（美投圈交易明细、受限文字稿、答疑答案）
依赖服务器 `/etc/meitou-mirror.env` 里的 `MEITOU_BEARER_TOKEN`。这条 JWT **只有 24 小时有效期**，
而源站账号 `729187288@qq.com` 绑定的是 **Apple 登录**：服务器上的 headless 续期脚本走到
`appleid.apple.com` 就无法完成双重认证（2026-09-14 起彻底失效，导致镜像静默降级两天）。

本工具把续期搬到**你自己的 Mac** 上：

1. 用专用 Chrome profile（`~/.meitou-refresh/chrome-profile`）人工登录一次（Apple 2FA 在真实浏览器里完成）；
2. 之后 launchd 每 6 小时无头打开源站，从 `localStorage.auth_token` / 请求头里抓最新 Bearer；
3. 本地验证（当场调 `gqlrealauth` 确认能拿到会员交易明细）→ 通过 ssh 原子写入服务器 `MEITOU_MIRROR.env` 并备份；
4. 服务器侧 `token-check.mjs` 复核 → 触发一次完整 `sync.mjs` → 轮询公开快照确认 `authExpired` 变回 `false`；
5. 每 30 分钟巡检镜像快照，降级连续两次（≈1h）就发告警，并自动尝试续期一次。

## 安装

```bash
tools/meitou-token-refresh/install.sh          # 复制到 ~/.meitou-refresh、装 playwright-core、加载 launchd
~/.meitou-refresh/bin/run.sh onboard           # 一次性登录（会弹 Chrome，请完成 Apple 登录）
```

## 日常命令

```bash
~/.meitou-refresh/bin/run.sh refresh --force   # 立刻续期一次并跑完整验证
~/.meitou-refresh/bin/run.sh monitor           # 立即巡检一次
launchctl list | grep meitou                   # 看两个任务
tail -f ~/.meitou-refresh/logs/refresh.log     # 续期日志
tail -f ~/.meitou-refresh/logs/alert.log       # 告警记录
cat ~/.meitou-refresh/state.json               # 最近一次状态
```

## 配置

`~/.meitou-refresh/config.json`：

| 字段 | 说明 |
| --- | --- |
| `minRemainingHours` | 服务器 token 剩余时间大于该值就跳过采集（默认 8，省得每 6h 都开浏览器） |
| `freshTokenHours` | 采集到的 token 至少剩这么久才算“新鲜”（默认 6） |
| `autoHeal` | 巡检发现降级时是否自动跑一次 `refresh --force` |
| `alerts.macos` | macOS 通知（默认开） |
| `alerts.serverchanKey` / `barkUrl` / `wecomWebhook` / `genericWebhook` | 任填其一即启用对应推送 |

## 告警触发点

- 浏览器 profile 里没有可用 token（会话失效）→ **需要重新登录**，跑 `run.sh onboard`；
- token 校验失败 / 服务器侧 `token-check` 非 0；
- 写入后快照仍是 `degraded`；
- 镜像快照 45 分钟没更新（同步卡住）；
- 恢复时发一条「已恢复」。

## 排障

- **onboard 抓不到 token**：确认窗口里真的登录成功（右上角出现头像/美投圈可见），且 profile 目录可写；登录后令牌存在 `localStorage.auth_token`。
- **refresh 报 no-token**：会话过期（Apple 会话通常能撑数周），重跑 `onboard` 即可。
- **推送成功但快照仍 degraded**：看 `refresh.log` 里 `token-check` 输出；若服务器出口 IP 被限，需要在服务器侧另行处理。
- **npm 安装失败**：本机 `~/.npm` 归属被 root 污染时，`install.sh` 用 `--cache ~/.meitou-refresh/.npm-cache` 绕开，无需 sudo。
- **停用**：`launchctl bootout gui/$(id -u)/com.meitou.token-refresh`（同理 `com.meitou.mirror-monitor`）。

## 相关服务器侧事实（2026-09-16 记）

- 同步：`/etc/cron.d/meitou-mirror-sync`，每 15 分钟，`/var/www/meitou-build/sync.mjs`，日志 `/var/log/meitou-mirror-sync.log`；
- 服务器自带续期 `meitou-token-refresh.timer`（每 6h）对本账号**不可用**，保留只为兼容将来换成邮箱+密码账号；
- 无 token 时 `sync.mjs` 回落到 `gqlrealanon`，只给公开内容：交易明细为空、答疑 11516→10371、受限文字稿标 `restricted`；
- 页面右上角同步芯片会显示「授权已失效 · 当前展示公开内容」，但没有任何推送告警（本工具补上）。
