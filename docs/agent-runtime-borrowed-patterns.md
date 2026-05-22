# Agent Runtime 借鉴清单

本文记录从 OpenAI Codex 与 Anthropic Claude Code 官方仓库中可迁移到 DeepFocus 的工程模式。

## 已落地

- **可恢复 Agent 事件流**：SSE 事件现在带稳定 `id`，并返回 `retry: 2000`，支持浏览器用 `Last-Event-ID` 续接；前端会先给 EventSource 8 秒恢复窗口，再切到轮询。
- **日志级进度**：每条 Agent log 可保存自己的 `progress`，前端 Agent Run blocks 不再把旧日志误标成当前总进度。
- **统一阶段投影**：前端 block 映射补齐 FSI / model builder / KYC / reconciliation 等 agent 名称，和后端事件相位保持一致。

## 高优先级候选

1. **项目级 Agent 指令**
   - Codex 使用 `AGENTS.md` 作为仓库级、目录级协作说明。
   - DeepFocus 可增加根目录 `AGENTS.md`，把后端启动方式、金融合规边界、测试命令、禁用自动下单等规则写清楚。

2. **插件/技能 Manifest**
   - Claude Code 官方插件采用 `.claude-plugin/plugin.json`、`commands/`、`agents/`、`skills/`、`hooks/`、`.mcp.json` 这样的结构。
   - DeepFocus 的 SkillCenter 目前是前端静态数组，建议迁移为后端可发现的 `skill_manifest`，字段包括权限、风险等级、输入输出、依赖 MCP、审批要求。

3. **审批与权限闸门**
   - Codex 的配置文档强调 config、hooks、托管策略分层。
   - DeepFocus 已有 MCP 风险等级和审批字段，可扩展到 Agent task：高风险工具、外部网络抓取、文件写入、支付/交易动作必须进入 `waiting_approval`。

4. **Agent Run Artifact Store**
   - 现在结果写在 `result_json`；建议追加 `agent_artifacts` 表，保存报告、证据包、估值模型、回测摘要、工具调用 transcript，支持按 artifact 版本回放。

5. **专业 Agent 工作流模板**
   - Claude Code 官方插件把 code review、feature-dev、PR review 拆成命令、子 agent、技能组合。
   - DeepFocus 可把“财报复核、估值审查、组合复盘、风险扫描”做成同样的模板化工作流，并允许用户在执行前预览步骤和权限。

## 来源

- OpenAI Codex: https://github.com/openai/codex
- Codex AGENTS.md docs: https://github.com/openai/codex/blob/main/docs/agents_md.md
- Codex config docs: https://github.com/openai/codex/blob/main/docs/config.md
- Anthropic Claude Code: https://github.com/anthropics/claude-code
- Claude Code plugins: https://github.com/anthropics/claude-code/blob/main/plugins/README.md
