# DeepFocus CoreAgent 运行边界

## 目标

DeepFocus 的对话、研报/新闻解读和圆桌任务共享一个 `CoreAgent` 生命周期：

`意图识别 → 能力/证据策略 → 只读工具或解读适配器 → 合规审校 → 响应投影`。

HTTP JSON、SSE 和历史轮询只是传输层，不再各自维护一套“是否取证、如何记录步骤、如何降级”的判断。原有的 `CloudResearchLLM`、`research_harness`、Dulus 圆桌和专题解析器继续作为可替换 provider/adapter；这样可以先统一治理，再逐步迁移实现，避免一次性改坏缓存、配额和既有响应结构。

## 当前实现

- `core_agent.py` 提供请求/结果/事件契约、profile、只读工具 allow-list、受限事件账本和适配器入口。
- `CloudResearchLLM.run_tool_agent` 接受显式 `allowed_tool_names`，工具清单和执行两处都 fail-closed；CoreAgent 默认只从固定金融白名单取交集，动态 MCP 不会因为自报名称就获得权限（后续若接入新 MCP，必须先注册并补策略测试）。
- `/api/agents/tool-research` 和 `/api/agents/orchestrator-chat` 共用同一条 CoreAgent 研究入口；旧的技能路由、比较口径、iFinD 隔离和额度闸保持在入口外层。
- 研报、新闻、深稿、期权、数据源条目、专业财报 RAG/分析、股票速析和 FinGPT 等用户路径以 adapter 方式挂入同一生命周期，返回结构保持兼容；专业财报模块的云模型调用由主进程 singleton 适配器注入。
- 旧 `research-loop` 仍保留为兼容投影；新代码不应再向其中添加独立的路由/会话/工具注册逻辑。
- 会员 `deep-research`、`research-loop`、海关分析和持久化任务中心等后台多阶段任务仍由旧 AgentRuntime/任务状态机驱动，属于后续迁移对象；本轮不把它们伪装成已经统一。

### 当前生产批次

2026-08-30 已完成两批兼容发布。同步入口（普通对话、编排、工具研究、研报视觉/深稿、新闻、期权、数据源条目、专业财报 RAG/分析/评测、一键检测及 FinGPT 专题）均经过 `CoreAgent`；原有缓存、配额和响应字段保持兼容。生产当前仍使用已配置的 `MiniMax-M3`，DeepSeek 仅作为可切换的 OpenAI-compatible provider，并未把原生 coding Harness 运行时放进 API 进程。

## Profile 与安全边界

CoreAgent 的 finance profile 只允许已注册的只读行情、财报、估值、证据和风险工具。shell、文件写入、删除、下单、任意 subprocess、未声明的 MCP 和浏览器凭据访问均不属于默认能力。任何新工具必须先声明权限并补 composition/policy 测试。

事件账本只记录运行 id、阶段、工具名、状态和长度等可审计元数据，不记录 API key、完整提示词或未经截断的用户附件。设置 `DEEPFOCUS_CORE_AGENT_LEDGER_PATH` 后可追加写入 JSONL；未设置时使用有界内存账本，避免把金融对话默认落盘。

## 为什么不直接把 DeepSeek Harness 原样替换进来

DeepSeek Harness 的可复用价值是插件化装配、append-only session log、分层 profile、受控工具执行和 turn/step 事件，而不是把 coding bundle 原样放进金融 API。当前 DeepFocus 是 Python/FastAPI，生产还要处理租户/配额/iFinD 隔离、SSE 取消、缓存和既有 schema；直接引入 SDK 会增加一个外部 runtime/JSON-RPC 生命周期，且其默认示例包含 shell/filesystem 能力。

因此本阶段先移植这些架构约束。若后续试运行原生 Harness，应单独做 sidecar：锁定 SDK 与 runtime 版本和完整性、使用无 shell/fs 的金融 profile、把财经工具映射为只读 MCP、由宿主负责 timeout/cancel/tenant policy，并用 shadow traffic 做结果与成本回归；不能把默认 `sdk`/coding bundle 直接暴露给用户。

## 后续迁移顺序

1. 把会员 `deep-research` 的 S0 取证阶段迁到 CoreAgent `research` profile，再迁移多空/裁决阶段；保留现有任务状态、配额和跨用户缓存。
2. 把旧 `research-loop` 改成 CoreAgent 的 `research` projection（保留现有 SSE 字段），并清理不再使用的前端兼容组件。
3. 用 durable session store 替换可选 JSONL（按租户加密/保留周期），再评估原生 DeepSeek Harness sidecar。
