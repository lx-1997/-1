# 上线包：长线牛股方法论 + 三表数据层

《价值投资之长线牛股》方法论 → bull_playbook 引擎 + 三表数据层，接入阿尔法机器人与 AI agent。
**未部署。** 按下面顺序逐层上，每步带校验。⚠️生产是实盘验证过的真钱系统，按部就班、勿整文件覆盖。

## 改动清单

| 层 | 文件 | 类型 | 上线方式 |
|---|---|---|---|
| A 新文件 | `bull_playbook.py` | 新增 | 整文件 scp |
| A 新文件 | `financial_statements.py` | 新增 | 整文件 scp |
| A 新文件 | `ifind_fin_probe.py` | 新增(校准工具) | 整文件 scp |
| B tracked(本会话前未改) | `eastmoney_data.py` / `ifind_api.py` / `agent_tools.py` | 改 | `clean-tracked.patch` |
| C tracked(夹带用户其它改动) | `llm.py` | 改 1 处 | 精确 str.replace(见下) |
| D ⚠️分叉 | `ai_fund.py` | 改 6 处 | 精确补丁，**禁整文件覆盖** |
| 测试 | `tests/*` + `tests/conftest.py` | 新增/改 | scp(conftest 顺带修隔离 flaky) |

## 步骤

### 1) A 层新文件
```bash
scp bull_playbook.py financial_statements.py ifind_fin_probe.py  服务器:.../deepfocus_api/
```

### 2) B 层补丁（这 3 个文件服务器若 == 仓库 HEAD 可直接 apply）
```bash
cd 服务器/.../backend
git apply --check deploy/bull-stock-longterm/clean-tracked.patch   # 先 check
git apply         deploy/bull-stock-longterm/clean-tracked.patch
# check 不过 = 服务器这几个文件也分叉了 → 改用 str.replace 逐 hunk 打（patch 里每个 + 块就是新增内容）
```

### 3) C 层 llm.py（只加一段系统提示；服务器 llm.py 可能与 Mac 不同，用 str.replace）
锚点 = `run_tool_agent` 里的 system 串。把：
```
"你是 DeepFocus 的资深投研分析师，具备工具调用能力。"
```
替换为：
```
"你是 DeepFocus 的资深投研分析师，具备工具调用能力，秉持《价值投资之长线牛股》的研究框架："
"好生意三标准、业绩增长关键字(大订单/涨价/扩产/反转/库存/景气)、护城河与进化力、ROE 生命周期、"
"投资对象五型、现金流八类型——看长线先看生意质量与护城河，再叠加催化剂与趋势买点。"
"具备工具调用能力。"   # ← 保留原句继续
```
并在 `"再据此作答，不得凭记忆编造数字。"` 之后插入：
```
"当问题涉及『是不是长线牛股 / 值不值得长期持有 / 护城河 / 成长质量 / 估值贵不贵』时，"
"调用 assess_long_term_bull 取该方法论体检（ROE 生命周期阶段+投资对象五型+真实估值+本站催化剂+牛股基因分），据此分析。"
```
（以 Mac 的 `llm.py` `run_tool_agent` system 段为准对照，整段替换最稳。）

### 4) D 层 ai_fund.py（⚠️服务器版与 Mac 严重分叉，按铁律：拉服务器版→对照 Mac→逐 hunk str.replace + count 断言 + 备份）
6 处改动（以 Mac `ai_fund.py` 为源）：
1. 顶部 `import` 加 `bull_playbook` + `_STAGE_CN`/`_TT_STAGE_CN` 两个映射表。
2. `_fetch_kline_ohlc` 默认 `points=55→250`；`_gather_md` 拉 250 点 + `_FUND_CACHE`/`_fetch_fundamentals`(走 `financial_statements.fetch_statements`) + 基本面 piggyback。
3. `_analyze`：消息面→`catalyst_profile` 分类；新增「趋势」(`trend_template`)+杯柄/利弗莫尔；新增「成长质量」(ROE阶段×现金流类型×好生意)；权重重排；买点+gate 纳入 pattern_buy；return 加 `catalyst_kind/trend_template/pattern`。
4. `_market_regime_now`（新函数，沪深300 vs MA60，缓存2h）。
5. `run_tick`：算 `regime`/`bear` → 买入 gate `_buy_allowed`、卖出收紧 trail + 指数止损；return 加 `regime`。
6. 服务器若缺对应 anchor（分叉）→ 该 hunk 跳过并人工核对，**绝不强改**。

> ai_fund.py 是 untracked 文件，无 git 补丁；必须拿服务器实际文件逐段比对后手打。建议：`scp` 服务器版到本地 → `diff` Mac 版 → 我（或你）按差异手工合并我的 6 处。

### 5) 重启前预检 + 冒烟
```bash
python -c "import deepfocus_api.ai_fund, deepfocus_api.financial_statements, deepfocus_api.agent_tools"  # import 预检
python -m pytest deepfocus_api/tests/test_bull_playbook.py deepfocus_api/tests/test_financial_statements.py \
                 deepfocus_api/tests/test_assess_long_term_bull.py deepfocus_api/tests/test_ai_fund.py -q
python -c "import asyncio,deepfocus_api.financial_statements as fs; print(asyncio.run(fs.fetch_statements('600519','CN'))['cashflow_type'])"  # 真数据冒烟
# 重启 systemd 服务
```

## iFinD（可选，权威源，默认关）
东财已全程可用，iFinD 不开也行。要开：
```bash
python -m deepfocus_api.ifind_fin_probe 600519 000651   # 校准指标 ID（只读）
export DEEPFOCUS_IFIND_FIN_INDICATORS='{...探针给的JSON...}'
export DEEPFOCUS_IFIND_STATEMENTS=1                       # iFinD 优先、东财兜底
```

## 回滚
- A 层删新文件；B 层 `git apply -R clean-tracked.patch`；C/D 层用 `.bak` 备份还原。
- 三表/方法论失败都是优雅降级（缺数据不计入），不影响机器人主流程；最坏全降级=回到改动前行为。
