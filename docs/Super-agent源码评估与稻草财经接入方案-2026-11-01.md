# Super-agent源码评估与稻草财经接入方案

结论：借鉴事件驱动、授权委托、任务恢复和结果交付机制，接入内部研究工作流；保持现有财务与策略验证程序的最终裁决权。此次完成源码评估和接口设计，尚未安装、运行或上线该原型。

## 已核实的项目

正确项目是 [deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)，固定提交 `6337fc823ce20251c8de8085948165c81aaa701e`，Git作者/提交者DeepSeek Harness Team，时间2026-10-31T12:05:39Z，MIT。官方README明确它是研究原型、非正式产品、非生产就绪，公开实现与内部/文章设计不完全相同。先前另一个Harness仓库评估独立保留，不能代替本项目。

已获取13份关键源码/文档。raw域名13次连接失败留档后，以官方Git blob API按固定tree内SHA读取，逐份校验Git blob SHA1及SHA256；未运行仓库脚本。证据保存在 `artifacts/dsh-superagent-review-20261101` 的提交元数据、tree及两份source-manifest。

## 可以复用的机制

| 上游实际实现 | 稻草财经建议落点 | 验收要求 |
| --- | --- | --- |
| Source游标、稳定Trigger ID、重叠轮询去重（src/sources/poll.ts） | 财报采集完成/校验结束/备份完成事件 | event_id绑定owner、来源、原文SHA与事件类型；同一事件只生成一个任务；来源故障不前移游标 |
| Intake→Eval→Mandate→Ticket→Assignment；repo.atomic（src/core/engine.ts） | 研究任务入口与持久化状态机 | 请求与委托先落库后执行；重启继续原任务，不能另建实验绕过失败 |
| 明确授权与策略版本，订阅只是上下文（docs/002-authorization.md） | 冻结研究委托 | 固定数据池、源码哈希、知识模式、参数集合、验证门槛、允许工具及预算；恢复时重验；PDF/网页文字不授予权限 |
| Router和独立Worker会话、后台工作占槽（docs/003-execution.md） | 复用quant_worker_slots.py与quant_research_store.py | 保持owner、进程scope、token及子进程存活约束；不能因前台返回便提前释放槽 |
| 同事务记录结果与outbox、幂等交付（src/app/outbox.ts） | 内部任务进度/SSE/结果通知 | 提交结果与待交付事件原子记录；重试只补交付，不重跑策略；取消后不得继续发布结果 |

平台已有研究任务生命周期、owner隔离、工作槽及进度事件，宜增量扩展，不再引入一套平行账户/风控系统。上游持久化接口不等于我们的SQLite schema，需通过适配层映射。

## 首个接入场景与状态流

财报原文已捕获 → 校验来源/身份/页码/单位 → 生成待复核事实 → 人工或确定性证据核验 → 不可变归档 → 独立备份读回 → 内部进度流。

事件建议字段：event_id、owner、source_kind、source_sha256、occurred_at、observed_at、mandate_digest、parent_job_id；事实/报告仅引用存档ID，不把原文或用户数据塞进公共通知。工单状态区分queued、running、awaiting_review、completed、cancelled、transient_failed、validation_rejected；阶段进度是实际完成的步骤，不能伪造推理过程或剩余时间。

网络超时等可按冻结次数/退避重试；原文勾稽失败、来源冲突、样本外失败必须终止并保留证据，不能自动换股、补零、调参或“修到通过”。新假设只能另建预登记研究，已看过的数据继续标为开发数据。

## 执行边界与接入验收

上游明确命令可用操作员OS用户权限执行，目录及wrapper不构成安全沙箱。因此接入时仅暴露白名单类型化工具，不给研究Worker任意shell、数据库写入、生产部署或券商凭证。默认无GitHub/飞书发消息权限；此次也未连接这些渠道。

最小适配层需依次验收：重复事件/崩溃前后只执行一次；事务回滚不漏事件；跨owner拒绝；旧委托或源码变更拒绝恢复；子进程未退出不重领；取消后结果不可覆盖；PDF提示注入不能授权工具；校验失败不触发参数搜索；磁盘不足保留partial且不标备份完成；通知失败只重试交付。完成这些后再做内部只读试运行。

该架构能降低人工盯任务的成本，不证明Alpha增强。策略真正变强仍以冻结的新数据验证及原门槛为准。当前两组原始失败实验不改写，未创建实盘交易或生产服务。

源码依据：[域模型](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/docs/001-domain.md)、[授权](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/docs/002-authorization.md)、[执行与恢复](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/docs/003-execution.md)、[引擎](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/src/core/engine.ts)、[outbox](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/src/app/outbox.ts)。本方案为基于源码的适配设计，不声称已集成。
