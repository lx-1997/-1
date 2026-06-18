# AI 模拟盘竞技场 · 引擎→前端字段契约 / 渲染交接

> 引擎(`backend/deepfocus_api/ai_fund.py`)已产出以下新字段,**前端 `deploy/ai-fund.html` 还没渲染**。
> 本清单给前端会话照着接。⚠️ 两个会话同改 `ai_fund.py` 已有撞车记录——前端改动尽量只动 `ai-fund.html`。

---

## A. `GET /api/ai-fund/arena` 新增字段

已对齐的旧字段:`strategies[]`、`champion`、`spread`、`benchmark{name,nav_pct,history}`、`is_trading_day/in_session/phase_label`。

### A1. 每个 `strategies[i]` 卡新增
| 字段 | 含义 | 建议渲染 |
|---|---|---|
| `max_drawdown_pct` | 最大回撤%(负数) | 在 `lbcard()` 的 `.lbmeta` 里加一格：`回撤 <b class="down">-X%</b>`，和胜率/平仓并列。**这是"收益高≠稳"的关键对照** |
| `history` | 归一化净值火花线点[] | (并发会话已加)卡内画 mini sparkline |
| `win_streak`/`days_running`/`alpha_pct` | 连胜/天数/超额 | 已可用，按需展示 |

### A2. 新增 `consensus[]` —— 「多数 AI 都在拿」
```
consensus: [{ symbol, name, hold_count, agent_total, holders:[{fund_id,name,emoji}] }]
```
建议：竞技场顶部加一条「🤝 AI 共识」横幅，展示 hold_count 最高的票 +
持有它的头像串(emoji)。文案：`5 个 AI 里 3 个在拿 比亚迪 🤖🦣🦅`。

### A3. 新增 `divergence[]` —— 「有人拿有人躲」(最有看点)
```
divergence: [{ symbol, name, split, bulls:[{fund_id,name,emoji}], bears:[{fund_id,name,emoji}] }]
```
建议：加一条「⚔️ AI 分歧」卡。文案：`茅台 · 看多 🗿磐石 vs 回避 🧲磁极`。
点开可跳到对应 agent 详情看各自理由。这是赛马最吸睛的内容,优先做。

---

## B. `GET /api/ai-fund/snapshot?strategy=<fund_id>` 新增字段

### B1. `persona` 扩展 + `roster[]`
`persona` 现含 `emoji/style/blurb`；顶层 `roster:[{fund_id,name,emoji,style,blurb}]`
可用于详情页顶部的「切换选手」chip(已有 `showStSwitch`，可直接用 roster)。

### B2. `latest_debate` —— 顶层精选辩论(featured)
```
latest_debate: { symbol, name, ts, debate:{...见 B3} }   // 没有则 null
```
建议：详情页加一个「🧠 最新多空推演」精选卡，直接渲染 verdict（见下）。

### B3. 每笔买入 feed item 的 `debate`（核心，最体现「思考精彩」）
```
feed[i].debate = {
  bull:     { thesis, key_args:[{point,evidence_ref}], catalysts, confidence },
  rebuttal: { rebuttals:[{targets_bull_point,verdict,reasoning}], net_lean, ... },
  verdict:  { decision(建仓|观望|放弃), conviction(0~1), net_lean,
              thesis(一句话总论), invalidation(止损/认错位), edge_reason, key_risk }
}
```
建议：在已有的 `evFull()` 交易详情里，买入项追加一个可折叠的「🥊 多空辩论」区块：
- 一行总论 `verdict.thesis` + 信心条 `conviction`
- 三段：🟢多头 `bull.thesis` / 🔴空头 `rebuttal.net_lean+strongest_bear_point` / ⚖️裁判 `verdict.decision`
- 高亮 `verdict.invalidation`(止损位)和 `verdict.edge_reason`(为什么选它)——这俩最值钱

### B4. 「优选」横向对比步骤 —— **无需改动**
每笔买入的 `thinking[]` 现在可能含一条 `{icon:"⚖️",label:"优选",text:"候选里挑它而非X…"}`。
现有思考链渲染(按 icon+label+text)**会自动显示**，前端不用动。✅

---

## C. 验证要点
- arena 空库时 `consensus/divergence` 为 `[]`(不报错)；有持仓后才有内容。
- `debate`/`latest_debate` 仅主账户(阿尔法)产出，且只在真实成交+辩论开关开(`DEEPFOCUS_AIFUND_DEBATE≠0`)时有；其它选手为 `null`，前端需判空。
- 所有新字段都是**附加**，旧渲染不受影响。
