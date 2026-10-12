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

### 平台操作入口

平台页面操作通过独立的 `platform_assistant.py` 执行边界接入五个现有聊天通道；普通投研继续使用原有 CoreAgent。当前能力目录有 184 个已注册 JSON 接口，覆盖 13 个业务域；37 个工作区页面可通过结构化导航打开。目录按精确 method/path 审核，不会自动暴露新接口。

模型先发现能力及 OpenAPI 参数，再请求调用。执行器通过本应用的 ASGI 栈调用原接口，沿用当前 JWT、角色、会员额度、数据归属及输入校验。模型不能传入凭据或归属身份。个人记忆限定 `scope=user`，共享资料写入需要管理员权限。

模型通道未返回原生工具调用时，使用严格的 `{tool, arguments}` JSON 计划兼容协议；计划仍进入同一个执行器和权限规则。仅参数对象、普通正文或没有回执的“已完成”声明不能触发操作。JSON 协议与原生调用共享单次执行记录。

明确要求添加自选、创建任务等常规操作可以直接执行；自选添加会先读取并保留原列表。删除、持仓台账变更、公开分享等操作生成账号绑定的十分钟确认卡片，只有点击后才执行。确认及请求 ID 使用 SQLite 保存，重复确认返回原回执，处理中或结果不明的操作不会重放。前端核验账号版本后显示真实回执，并刷新自选状态。

`GET /api/agents/platform/capabilities` 返回当前账号可用目录与交互边界。文件选择、微信扫码、设备订阅授权、原文下载和凭据配置需要页面交互，助手可以打开对应工作区；这些路径不宣称已经在聊天中完成。管理员运营、任意外部 MCP 和 shell 不属于此操作入口。

事件账本只记录运行 id、阶段、工具名、状态和长度等可审计元数据，不记录 API key、完整提示词或未经截断的用户附件。设置 `DEEPFOCUS_CORE_AGENT_LEDGER_PATH` 后可追加写入 JSONL；未设置时使用有界内存账本，避免把金融对话默认落盘。

## 为什么不直接把 DeepSeek Harness 原样替换进来

DeepSeek Harness 的可复用价值是插件化装配、append-only session log、分层 profile、受控工具执行和 turn/step 事件，而不是把 coding bundle 原样放进金融 API。当前 DeepFocus 是 Python/FastAPI，生产还要处理租户/配额/iFinD 隔离、SSE 取消、缓存和既有 schema；直接引入 SDK 会增加一个外部 runtime/JSON-RPC 生命周期，且其默认示例包含 shell/filesystem 能力。

因此本阶段先移植这些架构约束。若后续试运行原生 Harness，应单独做 sidecar：锁定 SDK 与 runtime 版本和完整性、使用无 shell/fs 的金融 profile、把财经工具映射为只读 MCP、由宿主负责 timeout/cancel/tenant policy，并用 shadow traffic 做结果与成本回归；不能把默认 `sdk`/coding bundle 直接暴露给用户。

## 后续迁移顺序

1. 把会员 `deep-research` 的 S0 取证阶段迁到 CoreAgent `research` profile，再迁移多空/裁决阶段；保留现有任务状态、配额和跨用户缓存。
2. 把旧 `research-loop` 改成 CoreAgent 的 `research` projection（保留现有 SSE 字段），并清理不再使用的前端兼容组件。
3. 用 durable session store 替换可选 JSONL（按租户加密/保留周期），再评估原生 DeepSeek Harness sidecar。
