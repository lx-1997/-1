---
name: deepfocus-prod-deploy
description: >-
  把 DeepFocus / daocaijing 平台的改动安全发布到生产服务器 39.105.214.141(后端 FastAPI +
  前端 CRA)。当用户说"上线/发布/部署/deploy/把这个改动发到生产/更新线上/改后端要重启/重建前端/
  把修复发出去/同步到服务器/push to prod"或要求把本仓库的后端 main.py、其它 deepfocus_api 模块、
  或前端(FinancialTerminal 等)的改动应用到 daocaijing.com 时使用。两条核心流程:①后端精确补丁
  (服务器 main.py 与 Mac 严重分叉,禁整文件覆盖);②前端全量重建发布。也覆盖发布前后的验证。
---

# DeepFocus 生产发布

生产服务器 `39.105.214.141`(与 DAO 财经同机),`ssh -o BatchMode=yes root@39.105.214.141` 直连(known_hosts 已有)。域名 `https://daocaijing.com`。**改动前先读项目 memory `deepfocus-prod-deploy` / `terminal-research-panel`** —— 那里有最新的分叉/坑记录。

核心铁律:**服务器后端代码与 Mac 仓库严重分叉**(不同血缘:Mac 有 weixin_bind 等服务器没有的集成),所以**后端永远只打精确补丁,绝不整文件 scp 覆盖**(覆盖过崩生产)。前端是同款 CRA 产物,可整包发,但要 overlay 不要 wipe。

---

## 流程 A:后端精确补丁(main.py 及其它 deepfocus_api 模块)

适用:改了 `backend/deepfocus_api/*.py` 里**已存在**的函数/逻辑,要同步到生产。

生效文件:`/opt/deepfocus/backend/deepfocus_api/main.py`(及同目录其它模块);服务 `deepfocus-api.service`(uvicorn `deepfocus_api.main:app` @127.0.0.1:8300,**无 --reload,改后必重启**);venv `/opt/deepfocus/venv/bin/python3.11`。`/root/*.bak*.py` 全是历史备份别动。

1. **先确认 Mac 与服务器目标代码块逐字一致**(否则锚点对不上):
   ```bash
   # 提取 Mac 侧目标块(按唯一起止锚点) → /tmp/mac_block.txt
   # 同样 awk 从服务器提取 → /tmp/srv_block.txt
   diff /tmp/mac_block.txt /tmp/srv_block.txt && echo IDENTICAL
   ```
   一致 → 可整块 str.replace 替换两边;不一致 → 只替换最小的、两边都存在的子串。

2. **先在 Mac 仓库改**(Edit),`python3 -c "import ast; ast.parse(open('backend/deepfocus_api/main.py').read())"` 验证语法。

3. **用 `scripts/precise_patch.py` 同步到服务器**(str.replace + count 断言 + 备份 + py_compile,见下)。它把 old/new 块从文件读入,避免 shell 引号地狱。

4. **重启 + 验证**(顺序敏感:8300 boot 慢,要轮询等 health):
   ```bash
   ssh root@39.105.214.141 'systemctl restart deepfocus-api.service
     for i in 1 2 3 4 5 6; do c=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/health); echo $c; [ "$c" = 200 ] && break; sleep 2; done'
   ```
   再针对改动 curl 对应端点(localhost:8300)确认新行为。

⚠️ `python -c 'import deepfocus_api.main'` 预检**挡不住 lifespan 崩溃**(懒导入不在顶层)。py_compile 只查语法。真要保险跑前台冒烟:`uvicorn ... --port 8399` 看 "Application startup complete"。但纯改 HTML 字符串/启发式函数这类不碰导入链的,py_compile + 重启后 health 200 即足够。

详细分步与历史坑见 `references/backend-patch.md`。

---

## 流程 B:前端全量重建发布(FinancialTerminal 等 src/ 改动)

适用:改了 `src/**`(终端前端),要上线。**注意:重建会发布当前 HEAD 的全部前端状态**——若 HEAD 比线上新,会顺带把未发布的改动一起推上去,**发布前必须告知用户并确认**(这是面向用户的不可逆操作)。

生效:nginx 静态根 `/var/www/deepfocus`。

1. **构建**(读 `.env.local`:`REACT_APP_TERMINAL_ONLY=true`、`homepage:"./"`、同源 apiClient):
   ```bash
   CI=false npm run build      # ❌ 别设 REACT_APP_API_BASE_URL(会烘焙死 IP→混合内容被拦)
   ```
   产物 `build/`。确认改动进了 bundle(中文被 Terser 转义,见坑③)。

2. **打干净 tar**(排除 mac AppleDouble `._*`,否则污染 www):
   ```bash
   COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='.DS_Store' -czf /tmp/df-fe.tgz -C build .
   tar tzf /tmp/df-fe.tgz | grep -c '/\._'   # 应为 0
   ```

3. **发布前安全核对**:`ls build/` 与服务器 `ls /var/www/deepfocus | grep -v '^\._'` 用 `LC_ALL=C sort` 后 `comm -23` —— "服务器独有文件"应为空才能放心(为空说明 overlay 不会漏掉任何服务器侧文件)。

4. **备份 + 叠加覆盖**(overlay,**不 wipe**,保留任何服务器独有文件):
   ```bash
   scp /tmp/df-fe.tgz root@39.105.214.141:/tmp/
   ssh root@39.105.214.141 '
     tar -czf /root/deepfocus-web.bak-$(date +%Y%m%d-%H%M%S).tgz -C /var/www/deepfocus .
     tar -xzf /tmp/df-fe.tgz -C /var/www/deepfocus          # LIBARCHIVE.xattr 警告无害
     find /var/www/deepfocus -name "._*" -delete
     chown -R 501:games /var/www/deepfocus
     grep -oE "static/js/main\.[a-z0-9]+\.js" /var/www/deepfocus/index.html | head -1'
   ```

5. **验证(必须从服务器本地 + 浏览器 UA,见坑②)**:
   ```bash
   ssh root@39.105.214.141 'UA="Mozilla/5.0 ... Chrome/149 ..."
     curl -s -k -A "$UA" -o /dev/null -w "home %{http_code}\n" https://127.0.0.1/ -H "Host: daocaijing.com"
     curl -s -k -A "$UA" -o /dev/null -w "ai-fund %{http_code}\n" https://127.0.0.1/ai-fund -H "Host: daocaijing.com"'
   ```
   index.html 是 no-cache,用户 F5 即吃新 bundle。

详细与回滚见 `references/frontend-deploy.md`。

---

## 三个反复踩的坑

① **`/ai-fund` 是独立目录** `/var/www/daocaijing/ai-fund.html`(nginx `location = /ai-fund alias`),**不在** `/var/www/deepfocus`——发前端不会碰它,但若误 wipe 别处或动 daocaijing 目录会断。AI 模拟盘的 HTML 在 `deploy/ai-fund.html`,前端可整文件 scp(与后端不同)。

② **裸 curl 验证会失败**:nginx `00-security-zones.conf` 的 `$bad_bot` map 拦 `curl/` UA → 444 → 本机 curl 得 exit 56 / HTTP 000。这**不是故障**。验证必须带浏览器 UA,且从服务器本地(`https://127.0.0.1 -H "Host: daocaijing.com"`)或直接看 on-disk 文件;也可看 nginx access.log 真实用户状态码。

③ **bundle 里中文被转义**:CRA/Terser `ascii_only` 把中文输出成 `\uXXXX`,`grep 中文` 必然 0 命中。要确认某中文串是否进了 bundle,用 Python 找转义形式:
   ```python
   esc = "".join("\\u%04x" % ord(c) for c in "提及个股")
   # 在 build/static/js/*.js 里找 needle 或 esc
   ```

④ 服务器自己 curl 自身公网 https 因出网代理可能返 000,对生产判断无意义——看真实用户 access.log。

---

## 收尾

- 发布后把本次改动 commit(只 `git add` 自己改的文件,仓库里常有并发会话的无关改动别一起提交);当前多在 `feat/*` 分支,commit 末尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。是否 push 由用户定。
- 把"这次改了什么、生效在哪、踩没踩新坑"回写进 memory `deepfocus-prod-deploy` / 对应功能 memory。
