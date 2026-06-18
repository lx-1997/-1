# AI 模拟盘竞技场 · 部署 Runbook(diff-first, 可回滚)

> ⚠️ 这是改动 **live 交易引擎** 的部署。`ai_fund.py` 与服务器**严重分叉**(服务器独有行),
> **禁止盲目整文件 scp**。必须先 diff、确认服务器独有改动已保留,再同步。出问题能一键回滚。
>
> 本地源:`feat/ai-fund-arena` @ `a18d77b`。生产:`39.105.214.141`(与 DAO 同机),
> 路径 `/opt/deepfocus/backend/deepfocus_api/`,服务 `deepfocus-api.service`(无 --reload,改后必重启),
> py3.11 venv。**这个 runbook 要在能 SSH 到服务器的机器上跑**(当前开发沙箱出网被封,够不到)。

变量(按需改):
```bash
SRV=root@39.105.214.141
APP=/opt/deepfocus/backend/deepfocus_api
VENV=/opt/deepfocus/backend/.venv/bin/python   # 按服务器实际 venv 路径
LOCAL=backend/deepfocus_api                     # 本地仓库内路径
TS=$(date +%Y%m%d-%H%M%S)
```

---

## 0. 备份(任何改动前)
```bash
ssh $SRV "mkdir -p /opt/deepfocus/_bak/$TS && \
  cp $APP/ai_fund.py $APP/main.py $APP/auth.py /opt/deepfocus/_bak/$TS/ && \
  cp /var/www/deepfocus/ai-fund.html /opt/deepfocus/_bak/$TS/ 2>/dev/null; \
  ls -la /opt/deepfocus/_bak/$TS/"
```
> ⭐ 同时备份当前 sqlite(模拟盘真实持仓/历史,别丢):
```bash
ssh $SRV "cp $APP/../.ai_fund.sqlite3 /opt/deepfocus/_bak/$TS/ 2>/dev/null; ls -la /opt/deepfocus/_bak/$TS/"
```

## 1. ⭐ 分叉闸门:diff 服务器 ai_fund.py vs 本地(最关键一步)
```bash
ssh $SRV "cat $APP/ai_fund.py" > /tmp/srv_ai_fund.py
diff /tmp/srv_ai_fund.py $LOCAL/ai_fund.py | less   # 或 git diff --no-index
```
**人工确认**:服务器独有的行(本地没有的)是不是都是**过时的、已被本地重构覆盖**的?
- 若服务器有本地缺失的**有效改动**(如某个生产专属修复)→ **先把它手动 port 进本地 ai_fund.py,再继续**,别让同步抹掉它。
- 全部确认无遗漏后,才进入第 2 步。**这一步没过,不许往下走。**

## 2. 同步 ai_fund.py(确认无遗漏后)
```bash
scp $LOCAL/ai_fund.py $SRV:$APP/ai_fund.py
```

## 3. main.py / auth.py:精确补丁(别整覆盖,它们也分叉)
本地相对 `be7df0b` 的 arena 改动 = main.py(+136:import ai_fund / lifespan 起 run_ai_fund_trader /
`run_ai_fund_trader` 跑全 ROSTER / `_aifund_snapshot` + `/snapshot?strategy=` + `/arena` 端点)、
auth.py(+3:两条 ai-fund 公开放行)。生成补丁并在服务器手动核对应用:
```bash
git diff be7df0b a18d77b -- backend/deepfocus_api/main.py > /tmp/main.arena.patch
git diff be7df0b a18d77b -- backend/deepfocus_api/auth.py > /tmp/auth.arena.patch
# 把补丁 scp 上去,服务器 `patch -p1 --dry-run < ...` 预演 → 成功再去 --dry-run 实施;
# 若 hunk 因分叉对不上,改用「逐段 str.replace/手动插入」并 count 断言(同 admin-membership 铁律)。
```
> 关键插入点:① `from . import ai_fund` ② lifespan 内 `ai_fund.init_ai_fund_db()` + `asyncio.create_task(run_ai_fund_trader())` 并加进 gather ③ `run_ai_fund_trader` 整个函数 ④ 三个端点。auth.py 在公开白名单数组里加两行。

## 4. 前端:ai-fund.html 整文件 OK(前端不分叉)
```bash
# 确认服务器静态目录(nginx root),示例:
scp deploy/ai-fund.html $SRV:/var/www/deepfocus/ai-fund.html
```
> ⚠️ 若并发会话还在改 ai-fund.html,**等它收口后用最新版**,别发半成品。

## 5. import 预检(重启前必做,挡住语法/导入错误)
```bash
ssh $SRV "cd /opt/deepfocus/backend && $VENV -c 'import deepfocus_api.ai_fund, deepfocus_api.main, deepfocus_api.auth; print(\"import OK\")'"
```
失败 → 立刻回滚(第 8 步),别重启。

## 6. 重启服务
```bash
ssh $SRV "systemctl restart deepfocus-api.service && sleep 3 && systemctl is-active deepfocus-api.service"
ssh $SRV "journalctl -u deepfocus-api.service -n 40 --no-pager | grep -i ai-fund"   # 看 5 个智能体启动日志
```

## 7. 冒烟验证(线上)
```bash
curl -s 'https://daocaijing.com/api/ai-fund/arena' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("strategies:",len(d.get("strategies",[])),"champion:",d.get("champion"),"consensus:",len(d.get("consensus",[])))'
curl -s 'https://daocaijing.com/api/ai-fund/snapshot?strategy=mammoth' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("persona:",d["persona"]["name"],d["persona"]["emoji"],"dq:",d["data_quality"]["level"])'
curl -s 'https://daocaijing.com/api/ai-fund/snapshot' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("main nav_pct:",d["nav_pct"],"positions:",d["position_count"])'  # 主账户历史还在?
```
预期:arena 出 5 选手 + champion;mammoth=猛犸🦣;**main 的历史持仓/净值没丢**。

## 8. 回滚(任一步出错)
```bash
ssh $SRV "cp /opt/deepfocus/_bak/$TS/ai_fund.py $APP/ && cp /opt/deepfocus/_bak/$TS/main.py $APP/ && \
  cp /opt/deepfocus/_bak/$TS/auth.py $APP/ && systemctl restart deepfocus-api.service && systemctl is-active deepfocus-api.service"
```

---

## 备注
- **DB 迁移自动**:`init_ai_fund_db()` 自动建 `aif_debate` 表 + 给 4 个新 agent 播种 `aif_state` 行(幂等),不动 main 的历史。无需手动 SQL。
- **新 agent 发车时刻**:= 部署时刻(main 保留历史 → 排行榜 main 有头部优势,各算各的 nav%,诚实)。要绝对公平可清空 4 个新 agent 的 aif_state/nav 重新等齐,但没必要。
- **辩论开关/成本**:`DEEPFOCUS_AIFUND_DEBATE=0` 可关。开着时仅主账户、每轮 ≤1 笔买入跑 3 次 LLM(MiniMax M3),24h 去重,成本可控。
- **每轮 5 个 agent 顺序跑**:数据缓存(东财日线/资金流/行情)共享,首个预热后其余近零外网,不会 5× 压垮东财。
