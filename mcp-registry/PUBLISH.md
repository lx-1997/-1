# 稻草财经 MCP · 各市场提交材料与步骤

提交物料已备齐：`server.json`（官方 Registry manifest）。以下按市场逐个给出**精确的提交内容**（直接复制粘贴）。提交需各平台账号，属人工操作；本文件是操作脚本。

---

## 通用物料（各市场都会要）

**名称**：稻草财经 MCP（Daocaijing Finance MCP）

**一句话简介**：A股复盘 · 个股速判卡 · 研报元数据 · 题材映射 · 行情 · 会取真数的投研 AI 问答。

**完整描述**：
> 稻草财经（daocaijing.com）投研 MCP。12 个只读工具：今日/指定日期 A股复盘、个股证据速判卡（确定性引擎）、资讯与研报检索（元数据）、题材→受益股与个股→题材反查、A/H/美股行情快照，以及 ask_ai——会调工具取真实行情与财务数据再回答的投研问答。OAuth 2.1 浏览器授权，登录即用；免费，AI 问答配额与官网会员一致。内容仅供研究参考，不构成投资建议。

**MCP URL**：`https://daocaijing.com/api/mcp`（Streamable HTTP，OAuth 2.1）

**分类/标签**：finance, market-data, investment-research, china-a-shares / A股、投研、行情

**图标**：站内 favicon/起一个 512×512 LOGO（`https://daocaijing.com` 现有资产）

**支持渠道**：官网 daocaijing.com；开发者文档 https://daocaijing.com/developers

---

## 1. 官方 MCP Registry（modelcontextprotocol）

1. `pip install mcp-publisher` 或用 GitHub 账号登录 registry.modelcontextprotocol.io
2. 校验 manifest：`mcp-publisher validate mcp-registry/server.json`（若 schema 字段有出入以校验器为准微调）
3. 发布：`mcp-publisher publish mcp-registry/server.json`
4. 注意：`name` 需要拥有对应域名（daocaijing.com）做 DNS/whois 校验，走官网提示的域名验证流程

## 2. PulseMCP（pulsemcp.com）

- 提交地址：pulsemcp.com → "Submit an MCP Server"
- 表单填上面通用物料；Remote URL 填 MCP URL；认证选 OAuth
- 是否开源选 No（服务端闭源，协议公开）

## 3. mcp.so / MCP 聚合站（smithery.ai、glama.ai/mcp 同类）

- mcp.so：提交 GitHub 仓库或站点 URL + 描述（通用物料）
- Smithery：需要提供 smithery.yaml（配置预设）。模板：
  ```yaml
  # smithery.yaml
  startCommand:
    type: remote
    url: https://daocaijing.com/api/mcp
    configSchema:
      type: object
      properties: {}
  ```
- Glama：登录后 "Add server" 填远程 URL

## 4. Cursor 目录（cursor.com/directory）

- Cursor 设置 → MCP → 已可被用户手动添加；目录收录走 cursor.com 提交入口（或其 GitHub cursor.directory 仓库 PR）
- PR 内容 = 通用物料 + slug `daocaijing`

## 5. Cherry Studio（国内主流 MCP 市场）

- Cherry Studio 的 MCP 市场支持直接添加远程服务器；官方收录渠道在其 GitHub discussions / 合作表单
- 填通用物料中文版即可，OAuth 客户端原生支持

## 6. Claude 目录（claude.ai → 连接器目录）与 ChatGPT connectors

- Claude：已满足 OAuth 2.1（MCP 规范授权框架）；目录收录提交至 Anthropic 的 connector 目录表单（Partnerships），填通用物料
- ChatGPT：Settings → Connectors 支持远程 MCP（OAuth）；收录同样走表单
- 两家均无需代码改动，等收录审核即可

---

## 发布前自检清单

- [x] /api/mcp 401 带 WWW-Authenticate resource_metadata（自动授权发现）
- [x] /.well-known/oauth-protected-resource、/.well-known/oauth-authorization-server 可公网访问
- [x] 动态客户端注册 / PKCE S256 / refresh 轮换（生产全舞步已验证 2026-10-08）
- [x] tools/list 12 个工具、描述中文
- [x] nginx 对 /api/mcp、/api/oauth/、/.well-known/ 豁免 UA/前端标识闸
- [ ] mcp-publisher validate 通过（提交时按校验器微调 server.json）
- [ ] 512×512 LOGO 上传至站内固定地址
