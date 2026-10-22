# 快讯 / 文章停更：采集链路只读诊断

当用户说“快讯/文章又不更新”“只显示昨天内容”或要求恢复新闻采集时读本参考。先定位断点，再确定修复范围；本流程本身只读，不登录刷新 token、不重启服务、不发钉钉/飞书/微信。生产后端继续遵守主技能的精确补丁规则，不能用 Mac 文件覆盖服务器整文件。

## 先核对四层证据

分别检查 `realinfo` 与 `article`，不要由其中一类推断另一类也停更。每层记录采样的北京时间、来源、`source_id`、原始发布时间和标题，沿同一条消息对齐；不同库的自增 `id` 不能直接比较大小。

| 层 | 读取与比较 | 能定位的断点 |
| --- | --- | --- |
| 1. 上游及原始时间 | 只用既有 token 查询 `realinfoList` / `articleList`；记录 HTTP 状态、业务 `code`、日期分组、`id`、`createtime`、`createtime_text`，比较 epoch 与原始日期文本 | 上游本身停更、鉴权静默降级、日期被下游改写 |
| 2. DAO 原始事件库 | 用 SQLite `mode=ro` 查询 `events` 中各类型最新若干条的 `id/type/source_id/source_time/inserted_at`；按实际 schema 检查原始 payload | 上游已有新消息而 DAO 未入库，先查采集进程 |
| 3. DeepFocus bridge | 核对实际启用配置、源库路径/源 API、bridge 断点、日志与 `realtime_messages` 中同一来源消息 | DAO 已入库而 bridge 未跟上，查断点、转换与轮询 |
| 4. 页面所用 API | 使用已有合法会话读取 `/api/realtime/messages`，按快讯/文章各自实际查询条件检查返回的来源与时间，再核对页面 | 数据库与 bridge 已追平而 API/页面缺失，查接口筛选、权限、缓存与前端 |

先从运行环境确认路径：bridge 可用 `DEEPFOCUS_DAO_DB_PATH` 直读 DAO 库，也可走 DAO `/v1/events`；断点路径可由 `DEEPFOCUS_DAO_BRIDGE_STATE_PATH` 覆盖。不要把本地默认值当作当前生产配置。数据库检查使用只读 URI，避免普通 SQLite 连接创建空库或改变 journal 设置。

2026-10-15 只读验证过的生产路径为 DAO `/DAO财经/.dao_events.sqlite3`（`events`）、bridge `/opt/deepfocus/backend/.dao_bridge_state.json`、终端 `/opt/deepfocus/backend/.realtime_messages.sqlite3`。API 可从 `127.0.0.1:8300` 查询 `/api/realtime/messages?topic=快讯&limit=3` 与 `topic=文章&limit=3`（请求参数按 URL 编码）。当时 bridge `after_id=246095` 与 DAO 全局最大事件 id 追平，分类断点 `after_article_id=246090`；这些是事故现场例子，下次须重新读取。

把原始 `createtime` 换算到 `Asia/Shanghai` 后与 `createtime_text`/日期分组交叉检查。保留上游原值及校正标记；`inserted_at`、bridge 入库时间和 API 接收时间都不能证明新闻发布时间新鲜。发现 `_source_clock_shift_days` 时，直接比较上游原始响应，不能仅相信下游 `source_time`。

## 核对服务与运行时 token

2026-10-15 事故时，采集脚本位于 `/DAO财经/循环推送.py`；`dao-realinfo` 跑快讯，`dao-article-futou` 跑文章，`dao-article-report` 也运行文章/研报脚本。服务名称与实际角色应通过 `ExecStart`、进程和配置重新确认，不能因为服务 `active` 就判定数据正常。

1. 读取服务的 PID、启动时间、`ExecStart` 和 drop-in 文件名。日志可能经 shell 重定向到 `/DAO财经/logs/realinfo.nohup.log`、`article_futou.nohup.log`，因此 `journalctl` 无输出不等于服务没有日志。
2. 读取主进程 `/proc/<pid>/environ` 时，只输出相关变量是否存在及非敏感配置。检查 token 来自进程环境、文件还是脚本兜底；比较 token 是否相同即可，不输出内容、完整环境、凭据文件或包含凭据的源码行。
3. 检查 `/DAO财经/.api_token` 的修改时间与进程启动时间。文件比进程新时，继续看读取函数是否缓存，不能假设进程已加载新值。
4. 比较 systemd 的最终环境与进程实际环境。该事故中虽然存在 `override-token.conf`，`futou-poll.conf` 的 `UnsetEnvironment=FUTOU_API_TOKEN` 已使两个采集进程没有该环境变量；配置文件存在不能证明它正在生效。
5. 用现有配置中的 token 分别查询两个列表接口，固定少量 `rows`，只打印上述白名单字段。不要调用采集脚本的自动刷新封装来做“只读”探测，它可能触发账号登录或发送；直接使用 `requests.get` 查询既有接口。

接口地址：

- `https://backend.futoucaixin.cn/api/index/realinfoList`
- `https://backend.futoucaixin.cn/api/index/articleList`

查询参数为 `page=1`、少量 `rows`、`keyword=""`，鉴权通过 `token` 请求头。读取 `realinfoList` 的 `data.grouped_data`，文章读取 `data.data`。HTTP 200 与业务 `code=200` 都要检查，且还必须核对原始发布时间。

## 已验证陷阱：旧 token、永久缓存与日期造新鲜

以下是 **2026-10-15 的事故采样例子**，不是后续运行时应期待的“最新 id/日期”：

| 既有 token / 接口 | 当时的原始响应 |
| --- | --- |
| 当前文件 token / 快讯 | HTTP 200、`code=200`，日期分组 `2026-10-15`，`id=66047`，`createtime_text=2026-10-15 15:12:31`，`createtime=1792048351` |
| 旧 override token / 快讯 | HTTP 200、`code=200`，日期分组 `2026-10-14`，`id=65304`，`createtime_text=2026-10-14 15:19:09` |
| 当前文件 token / 文章 | HTTP 200、`code=200`，`id=10805`，原始时间 `2026-10-15 14:52:33` |
| 同一旧 token / 文章 | HTTP 401、`code=401` |

这解释了为什么文章可能正常、快讯却断供：`get_api_token()` 把文件 token 缓存在 `_api_token_cache`；当时 `_get_json_with_token()` 首次尝试使用 `force_reload=False`，只在鉴权失败重试时重读文件。快讯的旧 token 静默降级仍返回 200，无法走到这一重载路径；文章的 401 则能触发重载。

当时 `.api_token` 修改时间为 10 月 15 日 00:03:41，快讯进程从 10 月 9 日运行。日志还持续出现“日期整体慢一天，已校正”的记录。`fetch_realinfo_items()` 在“无今日数据”检查之前调用 `_normalize_futou_realinfo_clock()`，把昨天的原始日期和 epoch 都加一天，又写入 `_source_clock_shift_days=1`，让旧内容通过今日/新鲜度门槛，进一步掩盖静默降级。诊断时不能把这种校正后的今日日期当成上游已恢复。

这里应严格区分代码风险与已入库事实：当时检查 DAO 最近 1000 条记录，日期校正标记计数为 0。日志证明校正分支执行过，尚不能证明这些改写日期的消息形成了新的数据库错误记录，不能把结论扩大为“DAO 已被污染”。原始时间可从 `raw_json` 的 `createtime`/`createtime_text` 与 `source_time` 交叉核对。

因此必须区分“现有文件 token 能取到今日消息”与“驻留进程确实使用它”；只改文件而不检查缓存，不足以验证恢复。不要为制造新鲜度把新闻时间加一天。

## 已验证陷阱：通知队列拖慢采集

同次事故只读观察到 `/DAO财经/.pending_all_queue.json` 已达 1000 条，快讯日志大量出现钉钉 `errcode=660026`（每分钟消息过多）及“延后 300 秒重试”。运行中的 `main()` 将抓取、逐条 AI/推送、延迟通知放在一个串行循环中；通知阶段遍历所有到期消息逐一 POST，每次请求超时可达 10 秒，失败项又按同轮时间统一延后 300 秒，没有每轮发送预算或失败熔断。队列的过期过滤只在启动读取时执行。

检查时只统计队列长度、到期数量、`send_at` 分布与文件更新时间，不输出完整消息队列。结合日志中最近抓取/入库记录判断是否被通知工作占满；“正在忙着重试”与“正在获取最新消息”不是一回事。文件中 `send_at` 暂无到期项可能只是刚完成一次整体延期，不能据此排除重试风暴。

## 修复与验证边界

仓库 `tools/dao-news-recovery/patch_collector.py` 读取私有源快照并输出单独候选文件。2026-10-15 15:28 已将六个唯一锚点的精确变更应用到采集服务：每次 GET 重读文件 token、保留上游原始时间、首轮立即抓取，延迟通知每轮一次，失败后全队列冷却 300 秒且失败项移到队尾，成功后间隔 5 秒。部署前后备份位于 `/root/dao-news-recovery-20261015-152750`；只重启 `dao-realinfo.service`，没有重启 DeepFocus API 或发布前端。候选 SHA256 为 `2b9beedd5856709362916d06646111730c5c7168a6ec2e7037a2ee5a7fd7bec5`，活动采集 PID 为 2010775。下次重新诊断时须核对实际版本与 PID。

本次在本机与生产同款 Python 3.8 分别通过 10 项 AST 隔离行为测试；随后 15:33 的自动周期继续收到 `source_id=66067`、原始发布时间 15:28:16 的消息，15:35 上游、DAO、bridge 和页面 API 对齐，真实浏览器显示新消息。对 14:05:34 至 15:28:16 的上游可用 21 条逐 ID 核对，缺失 0；没有另行执行历史回补。此结果只证明该观察窗口，不构成长周期可用性保证。

`recover_events.py` 默认只读审计，支持 `--since` 对照缺口；历史 cutoff 早于当前最新源时间时禁止 `--apply`，因为现有 bridge 用入库时间排序，直接回补会把旧闻广播为最新。先写入再修改时间无法撤回 SSE 与通知。它的 13 项隔离测试覆盖此守卫、日期边界、分页失败与自然键幂等；需要历史恢复时另设计保留原始时间的批次流程。

需要验证该候选时：

1. 将生产源快照保存在权限受限的临时位置，避免把内置私有配置放进 Git 或输出。使用独立 `--source`/`--output` 运行转换，检查六个唯一锚点及完整候选语法；原脚本有分叉时停在锚点不匹配处，重新确定最小补丁。
2. 在隔离环境用 stub 替代网络/发送函数，验证每次请求读取更新后的 token、昨天原始时间不会被改成今天、通知失败后冷却期间不继续发送、正常发送仍保留剩余队列。不得为测试发送真实群消息。
3. 语法与隔离验证只能证明候选性质，不能证明生产生效。只有实际应用并复核运行文件、服务加载及四层消息推进后，才可记录恢复结论；生产操作应沿主技能的精确补丁流程处理，并单独核对采集服务适用的重启方式，不能套用 DeepFocus API 的蓝绿命令。
4. 复核原始来源时间和 `source_id` 在 DAO、bridge、页面 API 连续推进；文章没有比上游落后时，不为“恢复”重复采集或重发文章。保留事故证据和仍未修复的限制。
