# AI 性能诊断与发布验收

用户反馈 AI 很慢时，先定位共用链路，再按本技能的精确补丁发布流程优化。以下流程已在 2026 年 10 月 12 日验证。

## 只读分层诊断

1. 从 `/run/deepfocus-active-port` 和 `systemctl show deepfocus-api@端口` 找运行实例。stdout 为 append 文件时读取 `/proc/PID/fd/1` 指向的日志；只看 journalctl 会漏掉模型与业务错误。
2. 分别测静态资源、localhost `/health`、直连模型的短回答首字与总时间，以及真实 JSON/SSE 业务。给 curl 加 `--max-time`，公网 nginx 带浏览器 UA。不能把短模型请求时间当整篇研报时间。
3. 读取实际模型池，只输出 provider、model、base_url、has_key；不得输出 key、完整环境或 JWT。区分 429 限流与 insufficient_quota，区分 text-only 的 400 与临时网络失败。
4. 连续读取 `vmstat 1 4` 的后续样本、API VmRSS/VmSwap，区分历史 swap 存量与正在换页。健康检查慢意味着还有应用或主机瓶颈，不能全归给模型。
5. 用 SQLite 只读 URI 查询 actual `PRAGMA journal_mode`、索引和少量链路样本。Mac 已用统一 WAL 入口不代表生产也已迁移。不要在线跑全表性能压测。
6. 对比完整生产源码，不只看本地。生产可能已经有网页整体 deadline、并行只读工具、DSH、审校和专属质量提示词。保留这些功能；不要拿过期本地代码解释现网行为。

## 共用调用层与业务边界

- 复用客户端时按 event loop、端点、凭证隔离，设置有界缓存；流式响应消费期持有 lease，并关闭 stream。禁止从另一个 loop 关闭活跃 transport。
- SDK 重试、模型 failover、JSON 修复和流式正文共用预算；总体预算过期后不得创建下一轮 coroutine 或新 fallback。
- 熔断共享到进程，额度耗尽用较长冷却或明确 reset；全部槽冷却时不能清空熔断。检查 SDK 直调，备用客户端与主模型名混用会产生 400；A 股复盘也要经过 `_completion`。
- 在线请求有有限队列；预热只在空闲时进入，并保留前台容量。取消后台 batch 时等所有子任务清理后释放，缓存写完再释放，避免同篇并发重算。
- SQLite 写入从 async handler 移到有界单 writer，await 提交保持读后可见。同步分享/结算仍保留同步语义。修剪当前序列，避免每若干次写入同步扫描全表；连接显式关闭。
- PDF LRU 同时限制条数、总字节和单件字节。不要把所有匿名内存直接归因于 PDF；没有 heap 证据时只说明可确认的预算问题。
- 降级缓存即便有四条逻辑线，也不得当完整结果长期命中；外层、内层、预热与 SSE 都检查降级标记。
- 扫描件 text/OCR 失败后，把本次失败复用于视觉降级，避免一次请求 OCR 两遍；下次请求仍可重新提取。
- 明确的稳定概念解释可走工具为空的 chat 档。公司、代码、具体数字、当前行情、附件、私人记录和操作继续取证；用全句语法白名单，避免只凭“是什么”跳过证据链。比较题保留足够轮次，并受同一总体预算限制。

## 测试与发布

1. 隔离工作树保留原树 WIP，从生产源码快照生成唯一 old/new 候选。检查 AST 未变业务方法，保留生产分叉。
2. 在实际生产 Python 环境测试候选，用临时 DATA_DIR、mock 模型。pytest 若未安装，复制纯 Python 测试依赖到临时 vendor 并用 PYTHONPATH，不修改生产 venv。
3. 测取消、队列满、预算、429 单次请求、视觉能力切换、缓存降级、重复 OCR、JSON/SSE 静态路由和比较题证据预算。先列出存在的测试文件，避免错误路径导致整批零测试。
4. `scripts/apply-exact-release.py` dry-run 零写验证全部原/结果 hash，apply 持 `/run/deepfocus-api-deploy.lock`、源备份和 SQLite backup API 快照。新增 helper 同批发布。
5. 使用 `/opt/deepfocus/bin/api-restart` 蓝绿闸门。发布后重新验证最终全部源码 hash、活动端口、日志、WAL、私有鉴权和真实模型/视觉/业务请求，再隔一段时间检查漂移。
6. 报告样本数量和限制。新进程 RSS 下降不能证明长期峰值，单条问答不能宣称所有 AI 的 P95 改善。额度、硬件容量、云端生成与 OCR 时间分别说明。

## 应用缓存字节预算与在线验收

- 仅限制条数的图片、搜索整池和富化响应仍可能占用大量内存；用 `bounded_cache.ByteBoundedTTLCache` 同时限制条数、总字节、单件字节，绝对 TTL 命中不续期，空闲时每 60 秒物理回收。估算器失败或超大项只拒收缓存，完整成功结果照常返回。
- 列表 fresh TTL 与 bounded stale grace 分开，默认 600 秒新鲜、一小时旧结果兜底；保留热门最新池，避免罕见搜索把热列表挤出。缓存返回副本，禁止富化后原地扩大未重计的值。
- 会员原文维持私有进程缓存，完整正文 30 天、stored-fallback 15 分钟。不得为了省内存放入公开 data_store，也不得截断原文。
- `/api/admin/runtime-cache-stats` 返回固定缓存名与纯数字，必须有 handler 级管理员守卫。观测 actual bytes/entries/expired/rejected；预算不是已占用量，重启后的 RSS 下降不能当永久释放证据。
- 请求和验收脚本的 ETag header 名大小写无关；生产同时验证首次 200、If-None-Match 304、匿名统计 401、管理员统计 200。没有可用外部图片 fixture 时明确只验了隔离完整字节路径。

## 分段耗时与研报速览并行（2026-10-15 验证）

- 先核对已发布源码和前端主包，再解释瓶颈；主工作树 WIP 可能落后于线上。`agent_chain.route=runtime` 是统计标签，不能据此推断实际路由进了 PlatformAssistant。
- 分别量模型选工具、真实取数、成稿和审校。若最终使用确定性报价/资料汇总 renderer，纯语法白名单命中后直接返回，保留真实工具回执和用户明确的简短要求；包含原因、历史、比较、估值或操作的题仍取证并审校。
- 速览不能串行 await 完成才起深稿。文本速览仅尝试后台容量，深稿使用前台容量；扫描件与文本失败后的视觉 fallback 会争用独占 `_batch_lease`，流式速览用 `allow_vision=False`，已有速览缓存仍可读。用真实 batch semaphore 的 barrier 及恢复旧调用的反向测试验证，避免只测两个假模型 coroutine。
- 同文生成用有界请求锁，锁内再查 canonical 缓存；取消要等待模型退出再释放请求额度，预热与点击共用成功结果。源版本 alias 只指向 canonical key，600 秒绝对 TTL，仍过质量门和原会员权限，refresh 绕过；不要保存第二份正文。
- 缓存、原文、队列、速览、深稿共用整体期限，等待期发送真实 elapsed tick。额度只在响应迭代器完成一次；深稿先完成或 AnyIO 断流时 cancel+await 全部 owned task。
- 把通过实际生产 Python 验证的候选、源码哈希和日志存到持久任务附件，避免中断后只剩临时目录。单条 adapter 样本、内部 helper 缓存、伪模型回归与真实页面 P95 分开报告；保留发布窗口失败探针，后续健康不能抹去它们。


## 研报仅增量预解读（2026-10-15 已验证）

- 用户要求“以后增量、存量先不管”时，首次非空有效 feed + 当时 archive 只登记持久 baseline，不能把最新200或归档全库直接作 pending；空 feed 可能是软故障，不初始化。只有后续首次出现的 file_id 入队，普通搜索 archive.upsert 不改变队列。seen/done/excluded 保留身份，归档8000裁剪、重启及重复快照都不能再入队。
- 排除条件仅精确 A 股名称+完整日期；完整名称表须保留短名和“机器人”，不复用聊天名称过滤或 _market_for 猜市场。HK/US 同形、未知名称、A 股带主题仍预解读。
- 后台必须生成前台默认 v3 deep 缓存，compact 是真实深稿投影；统一 POST/SSE/worker 完成校验，拒空正文、local/text-only fallback、vision_unavailable。cache落稳后再ack；busy/超时/取消不ack，并保留有界退避。
- 预热单task async_timeout + llm_deadline330；实测 anyio.fail_after 重复cancel会打断await清理，不可用来替换同task单次timeout。vision传background=True，由既有batch lease抢占并drain；finally同时回收原始page gather Future及children，避免未检索聚合异常。
- 生产增量发布fa6bd68f3c，首次baseline8000/pending0；173回归含真实临时SQLite并发和真实scheduler/runtime的边界与缓存，模型替身不等于真实新增端到端性能。发布保留源站40/日预算，本地PDF不重复计费。改archive schema须把archive.sqlite列入一致性备份。
- 此次监测370次有10次3秒超时；基线后105次200但max2367ms，既有服务器卡顿未完全定位，不能宣称零抖动或全面解决。代码提交e293c4c，验收docs/研报增量预解读验收-2026-10-15.md。
