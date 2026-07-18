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
