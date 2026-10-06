# 2026-10-06 生产服务器补丁归档

服务器（39.105.214.141）`/opt/deepfocus/backend/deepfocus_api/` 与 Mac 仓库代码严重分叉，
后端改动一律用 `scripts/precise_patch.py`（old/new 块 str.replace + 唯一性断言 + 自动备份 +
py_compile）打锚点补丁，**绝不整文件覆盖**。本目录按日期归档每次补丁的 old/new 块原文，
便于在服务器换机/重装时按序重放。应用方式：

```bash
scp -r <子目录> root@39.105.214.141:/tmp/
ssh root@39.105.214.141 '/opt/deepfocus/venv/bin/python3.11 /tmp/precise_patch.py \
  --file /opt/deepfocus/backend/deepfocus_api/<目标文件> \
  --old /tmp/<子目录>/<名>.old.txt --new /tmp/<子目录>/<名>.new.txt'
```

## auth-tool-research-whitelist（游客试问 401 修复）

- 目标：`auth.py` 的 `PUBLIC_EXACT`。问题：`DEEPFOCUS_AUTH_REQUIRED=true` 时全局鉴权中间件
  把匿名请求在进 handler 前 401，游客免费试问漏斗整体断裂。
- 内容：`/api/agents/tool-research` 与 `/api/agents/tool-research/stream` 加入白名单，
  额度闸由端点内 `_check_agent_quota` 承担（匿名 1 次/天→超额 403 引导登录）。
- 服务器备份：`auth.py.bak-20261006-134541`。已随 commit c768a9b 进入 Mac 仓同源代码。

## chain-metrics-wiring（研究链路任务长度/断链台账）

- 目标：`main.py`。01=助手函数 `_record_agent_chain`（锚点 `_check_agent_quota` 尾部）；
  02=管理员聚合端点 `GET /api/agents/chain-metrics`（锚点 `@app.post("/api/agents/feedback")`）；
  09/10=把记录挂在 `_record_ai_runtime_metrics` 唯一运行收口 + tracker 穿 `message`；
  11/12=JSON/SSE 两处 `_start_ai_runtime_tracker` 调用点传 `message=message`。
- **03–08 六块未在服务器应用**：服务器该区域已是新一代架构，锚点对不上；它们是 Mac 仓
  直挂实现（commit 903e4fc）所用的锚点，仅供 Mac 血缘重放。
- 服务器备份：`main.py.bak-*`（precise_patch 自动生成，打补丁时刻见服务器 /root 或同目录）。
- 验证：`.recall… ` 无关；看 `agent_chain` 记录（data_store）与
  `curl 127.0.0.1:8300/api/agents/chain-metrics`（管理员 token）。

## recall-ingest-wiring（研报/机构纪要同步断供修复）

- 背景：服务器新世代 main.py 丢失 recall_ingest 接线，`topic=研报/机构纪要` 自 2026-09-05
  断供一个月。模块文件 `recall_ingest.py` 从 Mac 仓整文件 scp（服务器原本不存在，非覆盖）。
- 01=import（接 `from .dao_bridge import ...` 后）；02=`_spawn_bg` 拉起（接 zsxq_health 后）；
  03=并入 `_bg_tasks` 元组尾（优雅关停）。
- 验证：`/opt/deepfocus/backend/.recall_ingest.sqlite3` 的 `recall_ingest_state.last_success_at`
  应每 75s 前进（journald 收不到 uvicorn 的 print，别用日志验证）。
