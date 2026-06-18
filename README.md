# 深度焦点 (DeepFocus) - 个股投研智库

一个现代化的个股投研智库平台，采用"钻井"模式，摒弃广度，追求极致深度。通过AI+人工精选的方式，为用户提供经过"蒸馏"的高价值信息流、专为投研设计的分析工具，并构建一个由真正的"铁粉"和专家组成的垂直社区。

## 🎯 项目愿景

**告别噪音，专注价值。成为你唯一需要的个股投研智库。**

## ✨ 核心功能

### 🔥 精炼信息流 (The Stream)
- **AI驱动的蒸馏**: 7x24小时聚合全网信息，通过AI自动去重、总结，并识别出关键信息
- **独家数据维度**: 整合供应链、专利、招聘、卫星图像等另类数据，提供独特视角
- **每日决策内参**: 每日由算法+分析师提炼3-5条最高价值信息，以"内参"形式精准推送
- **智能相关性评分**: 基于AI算法对信息进行0-100分相关性评分，帮助用户快速识别重要信息

### 👥 专家俱乐部 (The Club)
- **结构化讨论区**: 按照"技术"、"财报"、"市场"等议题开设分版块，引导深度、聚焦的讨论
- **高质量激励机制**: 设立"精华分析"认证、用户声望体系，让知识贡献者获得尊重和认可
- **情绪仪表盘**: 通过分析社区用户的"看涨/看跌"标签和讨论内容，实时生成社区情绪指数
- **专家认证体系**: 区分专家、分析师、投资者、新手等不同层级，确保讨论质量

### 🛠️ 研究工具箱 (The Toolkit)
- **智能事件日历**: 标注财报、发布会、锁仓解禁等关键日期，并关联相关新闻与社区讨论
- **个人投研笔记**: 用户可一键收藏任何信息、图表、评论，并添加自己的思考，形成个人专属的投研知识库
- **高级情景警报**: 用户可自定义复杂的警报规则，如"当供应链伙伴XX股价下跌超过5%时提醒我"

## 🎨 界面特色

- **现代化设计**: 采用 Ant Design 5.x，界面简洁美观，符合金融软件标准
- **响应式布局**: 支持不同屏幕尺寸，移动端友好
- **品牌化视觉**: 深度焦点专属的品牌色彩和图标系统
- **交互体验**: 流畅的动画效果和直观的操作反馈

## 🚀 技术架构

- **前端框架**: React 18 + TypeScript
- **UI组件库**: Ant Design 5.x
- **图表库**: Recharts
- **后端服务**: FastAPI + Finogrid
- **AI推理**: OpenAI / MiniMax / OpenAI-compatible 云模型
- **研报工作台**: Node.js 子模块，接入知识星球附件下载、文件库、研报解析和文件问答
- **桌面应用**: Electron
- **路由**: React Router
- **状态管理**: React Hooks
- **样式**: CSS + Ant Design
- **日期处理**: Day.js

## 📱 功能模块

### 🏠 仪表盘
- 账户概览和统计信息
- 实时K线图表
- 当前持仓和最近订单
- 技术指标显示

### 📈 行情中心
- 实时股票行情数据
- 股票搜索和筛选
- 自选股管理
- 涨跌幅排序

### 💼 交易功能
- 市价单、限价单、止损单
- 买入/卖出操作
- 订单摘要和费用计算
- 资金充足性检查

### 💰 投资组合
- 持仓明细和分布
- 盈亏统计和分析
- 订单历史记录
- 交易历史查询

### 👤 用户管理
- 多账户支持
- 用户登录/登出
- 账户切换
- 个人信息管理

## 🛠️ 安装和运行

### 环境要求
- Node.js 16.x 或更高版本
- npm 或 yarn

### 安装依赖
```bash
npm install
```

### 开发模式
```bash
# 启动后端 AI API
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-cloud.txt
cp .env.example .env
uvicorn deepfocus_api.main:app --host 0.0.0.0 --port 8300 --reload

# 跑 TradingAgents 这类长任务时，使用无 reload 的常驻后端
npm run backend:long

# 启动React开发服务器
npm start

# 在另一个终端启动Electron
npm run electron-dev
```

### 构建应用
```bash
# 构建React应用
npm run build

# 打包Electron应用
npm run electron-pack
```

## 📁 项目结构

更完整的阅读顺序和模块边界见 `docs/code-structure.md`。

```
src/
├── components/          # React 页面、布局和复用组件
├── components/home/     # 首页工作区拆分视图
├── components/common/   # 通用展示组件
├── components/agent/    # Agent 运行过程展示组件
├── services/            # 前端 API 服务层，按业务域聚合
├── state/               # App reducer 与初始化状态
├── utils/               # 纯工具函数和前端数据转换
├── types/               # TypeScript 共享类型
├── data/                # 演示数据
├── i18n/                # 多语言资源
├── App.tsx              # 应用装配、路由和顶层事件
└── index.tsx            # React 入口

backend/deepfocus_api/   # FastAPI 前端 API、Agent、行情、投研、风控、回测
backend/finogrid/        # Finogrid 子系统
modules/research-workbench/
                         # 独立研报工作台，由后端代理
```

## 🔑 演示账号

- 用户名: `demo`
- 密码: `demo`

## 📊 主要功能说明

### 实时数据
- 模拟实时股票价格更新
- 自动刷新K线图数据
- 实时计算盈亏和收益率

### 交易功能
- 支持市价单、限价单、止损单
- 自动计算手续费和总成本
- 资金充足性验证
- 订单状态跟踪

### 深度焦点特色
- **AI信息蒸馏**: 自动识别和总结重要信息
- **社区情绪分析**: 实时监控社区看涨/看跌情绪
- **专业投研工具**: 事件日历、投研笔记、智能警报
- **专家认证体系**: 确保社区讨论质量

### FinGPT 能力中心
- **个股投研**: 个股快照、社区内容、用户问题 → 投研摘要、催化因素、风险清单
- **金融情绪分析**: 新闻、公告、社区文本 → positive / neutral / negative
- **新闻蒸馏**: 多条资讯 → 决策摘要、关键信号、待验证动作
- **财报/研报解读**: 长文本报告 → 核心结论、风险、验证问题
- **RAG 知识库问答**: 基于 Finogrid 文档或传入资料回答问题并标注来源
- **预测与情景推演**: 参考 FinGPT-Forecaster 思路生成短期情景
- **稳定币/通道风险**: 面向 Finogrid 支付通道评估资产、地区、新闻风险
- **Agent 工作台**: 运营监督、审计治理、流程改进、内部支持、资金策略 Agent 摘要

所有文本型能力均支持手动输入和文件上传混合输入。当前文件抽取支持 `.txt`、`.md`、`.csv`、`.json`、`.pdf`、`.docx`、`.xlsx`、`.log`，后端接口为 `POST /api/fingpt/files/extract`。

当前默认 `DEEPFOCUS_LLM_PROVIDER=mock`，用于本地无 key 演示。接入云模型后，这些能力会走 OpenAI、MiniMax 或 OpenAI-compatible API。

### 数据源中心
- **服务器/API 数据源**: 注册自有服务器接口、行情 API、网页源，支持 `GET/POST`、headers、params、`{symbol}` / `{query}` 模板。
- **本地资料入库**: 上传研报、财报、纪要、表格、PDF、Word、Excel，抽取文本后进入证据库。
- **网页抓取资料**: 通过 URL 抓取网页或接口响应，保存来源、时间、可信度和关联标的。
- **关键词抓取**: 支持按关键词抓取公众号公开搜索结果；雪球抓取会尝试公开页面/接口，也支持配置 `DEEPFOCUS_XUEQIU_COOKIE` / `XUEQIU_TOKEN` 使用自有登录态请求。每个来源都有认证方式、风险等级、健康分和降级来源；遇到登录、验证码或 WAF 会记录失败原因并按策略走公开来源降级。
- **资料管理台**: 本地文件和远端资料统一展示，可按标的、来源、类型、关键词、tag 筛选。
- **标签管理**: 每份资料都能编辑标题、关联标的、可信度和多个 tag，便于后续 Agent 检索。
- **证据检索**: 按股票代码、关键词和 tag 检索资料，供核心链路自动引用。
- **持久化**: 数据源和证据保存到 `backend/.data_sources.sqlite3`。

投研任务对用户默认展示 4 个核心角色：`Orchestrator`、`Evidence`、`Analyst`、`Risk`。`Report Builder` 只作为输出层，不再作为同级 Agent 暴露。`Evidence` 内部负责同步服务器/API/网页源并检索本地上传和抓取资料；`Analyst` 统一吸收 TradingAgents、Financial Services Playbook、专业财报 RAG 和专题技能结果。报告中的结论会展示命中的证据来源；资料不足时会明确提示缺口。

### 专业财报研究内核（最小专业版）
- **财报解析复用现有上传链路**: `POST /api/pro-research/reports/upload` 会沿用文件抽取能力，同时把原文保存到数据源中心和专业财报库。
- **结构化指标库**: 自动抽取营业收入、归母净利润、扣非净利润、毛利率、ROE、经营现金流、资本开支等核心字段，保存到 `backend/.professional_research.sqlite3`。
- **引用型 RAG**: `POST /api/pro-research/rag/query` 先检索结构化指标和原文 chunk，回答必须带 `[M1]` / `[C1]` 引用；证据不足会明确拒答。
- **财报分析技能**: `POST /api/pro-research/reports/{report_id}/analyze` 输出核心指标、利润质量红旗、风险片段、追问清单和证据引用。
- **评测集**: `POST /api/pro-research/evals/run` 可基于入库报告自动生成最小回归用例，检查答案命中、引用覆盖和拒答保护。

这套内核先用轻量规则和 SQLite 保证可复现、可审计；配置真实模型后，摘要生成可以走云模型，但数字和引用仍由结构化库与证据库兜底。

### 研报工作台模块
- 主应用侧边栏新增 **研报工作台** 入口，默认通过后端 `http://127.0.0.1:8300/research-workbench/` 访问。
- 子模块位于 `modules/research-workbench`，保留独立 `tool-server.js`、`zsxq-downloader.js` 和 `tool-public/`，由 FastAPI 后端自动拉起并代理。
- 根目录 `npm install` 会通过 `postinstall` 安装工作台子模块依赖；如需手动补装，可执行 `npm run research-workbench:install`。
- 如需改用其他地址，可在 React 环境变量中设置 `REACT_APP_RESEARCH_WORKBENCH_URL`。

### 多市场智能选股与回测中心
- 侧边栏新增 **多市场策略**，面向 A 股、港股、美股分模块输出市场风格、板块意见、个股候选、大涨前概率排序、回测验证计划和中国大陆可运行依赖清单。
- 后端接口为 `POST /api/decision/multi-market`，默认使用本地规则编排，不把 GitHub、Yahoo Finance、OpenBB、Polygon、Alpaca、OpenAI 作为硬依赖。
- A 股模块规划为 `AKShare/Tushare/RQData + Qlib + RQAlpha`；港股模块规划为 `Futu OpenD/LongPort + vectorbt/Backtrader`；美股模块规划为 `Futu/LongPort/Wind/Choice/本地数据 + vectorbt/Backtrader/Zipline-reloaded`。
- 可选量化依赖清单位于 `backend/requirements-quant-cn.txt`。在中国大陆环境建议先配置国内 PyPI 镜像，再按需安装：
```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r backend/requirements-quant-cn.txt
```

### 期权雷达
- 侧边栏新增 **期权雷达**，面向美股/ETF 自选池输出 Put/Call Ratio、关键 OI 墙、Max Pain、ATM 跨式预期波动、IV 偏斜、期限结构和数据质量。
- 后端接口为 `GET /api/options/signals?symbols=AAPL,NVDA&horizon_days=45&max_expirations=3`。
- 默认数据源顺序为 `MarketData.app`、`Nasdaq Public Option Chain`、`Yahoo Finance public chain`。`MarketData.app` 建议配置 `MARKETDATA_APP_TOKEN` 或 `MARKETDATA_APP_API_KEY`；无 token 时只适合作为可用性兜底。
- 期权模块明确标记免费源延迟和字段缺口；Nasdaq 兜底通常没有 IV/Greeks，不能替代实时订单流或券商合规行情。

### 24h 投研任务中心
- **任务队列**: 投资研究、组合复盘、风险审查、观察名单监控任务统一进入队列。
- **常驻 worker**: 后端启动后自动运行 worker，持续拉取 `pending` 任务。
- **核心链路**: 默认收敛为 Orchestrator、Evidence、Analyst、Risk 四段；报告生成是输出层。情绪、情景、TradingAgents analyst/debate/trader、FSI workflow 等底层角色作为技能/引擎细节，不作为同级 Agent 暴露。
- **Financial Services Playbook**: 可选择参考 `anthropics/financial-services` 的工作流画像，把 market researcher、earnings reviewer、model builder、pitch agent、valuation reviewer、KYC screener、GL reconciler 等能力作为模板纳入 DeepFocus 队列与报告结构。
- **状态持久化**: 任务、日志、结果保存到 `backend/.agent_tasks.sqlite3`，可重启后继续查看。
- **长任务心跳**: TradingAgents 外部 runner 会定期刷新任务状态；运行完整分析引擎时建议用 `npm run backend:long`，避免开发模式 reload 中断子进程。
- **网页研究工具**: TradingAgents 的 news/social 分析师会注入 `deepfocus_web_search` 和 `deepfocus_read_url`，可在 Yahoo/Google RSS 限流或资料不足时主动搜索公开网页并读取可访问页面。
- **投资者报告**: 输出投资者摘要、证据来源、情景推演、风险纪律、行动清单、反证清单。

该模块用于提升投研流程和风险控制质量，不承诺收益，不自动下单。

### 用户体验
- 响应式设计，支持不同屏幕尺寸
- 现代化UI设计，符合金融软件标准
- 快速搜索和筛选功能
- 直观的数据可视化

## 🔮 开发说明

### 数据模拟
当前版本使用模拟数据进行演示，包括：
- 股票基础信息
- 实时价格数据
- K线图数据
- 用户账户信息
- 精炼新闻数据
- 社区讨论数据
- 投研笔记数据
- 事件日历数据

### 扩展功能
可以轻松扩展以下功能：
- 连接真实数据API
- 添加更多技术指标
- 实现实时通知
- 添加更多交易类型
- 支持更多市场
- 集成AI模型
- 添加更多另类数据源

## 📈 产品路线图

### 第一阶段 (当前)
- ✅ 基础交易功能
- ✅ 精炼信息流
- ✅ 专家俱乐部
- ✅ 研究工具箱

### 第二阶段 (计划中)
- 🔄 AI模型集成
- 🔄 另类数据接入
- 🔄 移动端应用
- 🔄 付费订阅系统

### 第三阶段 (未来)
- 📋 机构版功能
- 📋 数据API服务
- 📋 多市场支持
- 📋 国际化扩展

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目。

## 📞 联系我们

- 项目主页: [GitHub Repository]
- 问题反馈: [Issues]
- 功能建议: [Discussions]

---

**深度焦点** - 让投资研究更简单，让价值发现更高效。
