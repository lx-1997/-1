# 后端精确补丁 — 详细步骤与历史坑

## 为什么只能精确补丁
服务器 `/opt/deepfocus/backend/deepfocus_api/main.py` 与 Mac 仓库**不同血缘**:Mac 含 `weixin_bind` 等 9 处集成,服务器 0 处;且这些在 lifespan 内**懒导入**。整文件 scp 覆盖过 → 服务器缺模块 → 启动即 `ModuleNotFoundError` → 崩溃循环、8300 不监听、真实用户 `connection refused`(2026-06-16 约 6 分钟 API 中断)。**铁律:main.py 永远只打精确补丁,绝不整文件覆盖。** 本会话新建的独立模块(如某个全新 .py)可整传。

## 完整步骤
1. **定位生效文件**:`systemctl cat deepfocus-api.service` 看 ExecStart(`deepfocus_api.main:app`)+ WorkingDirectory(`/opt/deepfocus/backend`)。真实文件 `/opt/deepfocus/backend/deepfocus_api/main.py`。

2. **提取并 diff 目标块**(确认 Mac↔服务器逐字一致):
   ```bash
   # Mac:按唯一起止锚点 awk 出块
   awk '/^起始锚点/{f=1} f{print} /结束锚点/{exit}' backend/deepfocus_api/main.py > /tmp/old_block.txt
   # 服务器同法
   ssh root@39.105.214.141 "awk '/^起始锚点/{f=1} f{print} /结束锚点/{exit}' /opt/deepfocus/backend/deepfocus_api/main.py" > /tmp/srv_block.txt
   diff /tmp/old_block.txt /tmp/srv_block.txt && echo IDENTICAL
   ```
   - 一致:可整块替换。把改后的块写 `/tmp/new_block.txt`。
   - 不一致:别替换整块。改用最小子串(两边都存在的唯一片段)做 old/new。

3. **Mac 仓库先改 + 语法验证**:Edit 改 → `python3 -c "import ast; ast.parse(open('backend/deepfocus_api/main.py').read())"`。

4. **同步服务器**:
   ```bash
   scp /tmp/old_block.txt /tmp/new_block.txt scripts/precise_patch.py root@39.105.214.141:/tmp/
   ssh root@39.105.214.141 '/opt/deepfocus/venv/bin/python3.11 /tmp/precise_patch.py \
       --file /opt/deepfocus/backend/deepfocus_api/main.py --old /tmp/old_block.txt --new /tmp/new_block.txt'
   ```

5. **重启 + 等 health + 冒烟**:
   ```bash
   ssh root@39.105.214.141 'systemctl restart deepfocus-api.service
     for i in 1 2 3 4 5 6; do c=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/health); echo $c; [ "$c" = 200 ] && break; sleep 2; done'
   ```
   再 curl 改动对应的端点(localhost:8300)确认新行为(如分类/字段变化)。

## 坑
- **预检盲区**:`import deepfocus_api.main` 与 py_compile 都**挡不住 lifespan 崩溃**(懒导入/启动逻辑在运行期才执行)。碰导入链的改动要前台冒烟:`uvicorn deepfocus_api.main:app --port 8399` 看 "Application startup complete"。
- **重启慢**:uvicorn boot import 重,`systemctl restart` 后 8300 要几秒才 LISTEN,验证前轮询 health 或 `ss -ltn | grep 8300`。
- **比对两端代码别信 `ssh "cat" > /tmp`**(ssh stderr/xattr 噪声给过假 identical),用 scp 取回再 diff 或 md5sum 两端比。
- 验匿名/鉴权放行:无 token POST 空 body 看到 422/502(非 401)即证明过了鉴权层。

## 2026-09-15 llm.py 分叉审计(误整传后的事后验收方法)
本会话误将 Mac 工作树版 `llm.py` 整文件 scp 覆盖了服务器版(违反铁律),但事后审计证明无生产事故,并把验收方法沉淀于此:

1. **事故为何没发生**:服务器旧版是 2026-07 前的老血缘(无模型池/无 `_completion`),Mac 工作树版是 HEAD+~900 行未提交 WIP,恰好**依赖的 7 个懒导入模块服务器全有**、main.py 引用的 22 个 llm 属性全存在 → 导入链没断,重启成功。
2. **唯一真实损失**:服务器旧版有 8 处 `deepseek_harness`(服务器独有模块,Mac 无)调用钩子被覆盖掉;但该功能由 `DEEPFOCUS_AGENT_RUNTIME=dsh` 开启,生产上 `enabled()=False` 处于休眠 → 无实际影响。若哪天要启用 dsh,需把钩子重新打回。
3. **事后验收清单**(误覆盖后跑一遍再决定回滚还是放行):
   - `main.py` 全部 `llm.xxx` 属性 hasattr 核验(逐个列出 diff 两边模块级 def/class,`comm -23` 找"服务器独有且被引用"的符号);
   - 新版懒导入的每个 `.py` 模块服务器存在性核验;
   - 用旧备份(`.bak-*`)和 Mac diff 量化:只有被 main.py/其它模块引用的丢失才致命;
   - 重启后看 `/var/log/deepfocus-api.log` 的 `[headline] 评选完成` 持续成功 + 无 `ModuleNotFoundError`。
4. **备份命名惯例**:`llm.py.bak-pre-qwen3-20260915`、`.model_config.json.bak-minimax-20260915`,回滚直接 `cp` 回原名+重启。
5. **当前线上 llm.py = Mac main 分支 d4b069c 的血缘**(2026-09-15 起两版重新对齐,后续对 llm.py 打精确补丁前仍要先 diff 服务器版确认分叉状态)。

## 百炼/Token Plan 模型配置(2026-09-15 起)
- 生产 LLM:`qwen3.6-flash` @ `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`,provider 写 `openai-compatible`(走通用 AsyncOpenAI,不带 MiniMax 专属参数)。配置在 `/opt/deepfocus/backend/.model_config.json`,**每请求热读,改完不用重启**。
- qwen3.x 默认输出 `reasoning_content` 且计入 max_tokens 与 Token Plan 配额(前车之鉴:MiniMax Token Plan 就是这么烧穿的)。`llm.py::_completion` 漏斗层已统一注入 `extra_body.enable_thinking=false`(省 87% completion tokens,防 JSON 截断);显式 `-thinking` 变体不关。
- qwen3.6-flash 支持图片输入(vision-OCR 研报解读路径可用)。
- 验证模型链路是否通:SSH 到服务器直接 `CloudResearchLLM().complete_json(...)` 跑一次,再看日志 `[headline]` 行,比从外面 curl 公网端点可靠。
