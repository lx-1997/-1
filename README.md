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

# 启动React开发服务器
npm start

# 可选：启动研报工作台模块
npm run research-workbench:install
npm run research-workbench

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

```
src/
├── components/          # React组件
│   ├── Dashboard.tsx   # 仪表盘
│   ├── Market.tsx      # 行情页面
│   ├── Trading.tsx     # 交易页面
│   ├── Portfolio.tsx   # 投资组合
│   ├── KlineChart.tsx  # K线图组件
│   ├── Header.tsx      # 顶部导航
│   ├── Sidebar.tsx     # 侧边栏
│   ├── Login.tsx       # 登录页面
│   ├── RefinedNews.tsx # 精炼信息流
│   ├── ExpertClub.tsx  # 专家俱乐部
│   └── ResearchToolkit.tsx # 研究工具箱
├── types/              # TypeScript类型定义
├── data/               # 模拟数据
├── App.tsx             # 主应用组件
└── index.tsx           # 应用入口

backend/
├── deepfocus_api/      # 前台调用的轻量云模型 API
├── finogrid/           # 合并自 FinGPT 的后台、账本、通道、MCP 与 Agent 模块
└── requirements-cloud.txt

modules/
└── research-workbench/ # 知识星球研报下载、文件检索和 AI 对话工作台
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
- **Agent 抓取资料**: 通过 URL 抓取网页或接口响应，保存来源、时间、可信度和关联标的。
- **关键词抓取**: 支持按关键词抓取公众号公开搜索结果；雪球抓取会尝试公开页面/接口，也支持配置 `DEEPFOCUS_XUEQIU_COOKIE` / `XUEQIU_TOKEN` 使用自有登录态请求。每个来源都有认证方式、风险等级、健康分和降级来源；遇到登录、验证码或 WAF 会记录失败原因并按策略走公开来源降级。
- **资料管理台**: 本地文件和远端资料统一展示，可按标的、来源、类型、关键词、tag 筛选。
- **标签管理**: 每份资料都能编辑标题、关联标的、可信度和多个 tag，便于后续 Agent 检索。
- **证据检索**: 按股票代码、关键词和 tag 检索资料，供多 Agent 自动引用。
- **持久化**: 数据源和证据保存到 `backend/.data_sources.sqlite3`。

多 Agent 任务执行时会先由 `DataSourceAgent` 同步服务器/API/网页源，再由 `EvidenceAgent` 检索本地上传和抓取资料。报告中的结论会展示命中的证据来源；资料不足时会明确提示缺口。

### 研报工作台模块
- 主应用侧边栏新增 **研报工作台** 入口，默认嵌入 `http://127.0.0.1:3927`。
- 子模块位于 `modules/research-workbench`，保留独立 `tool-server.js`、`zsxq-downloader.js` 和 `tool-public/`。
- 首次使用执行 `npm run research-workbench:install` 安装模块依赖；之后执行 `npm run research-workbench` 启动。
- 如需改用其他地址，可在 React 环境变量中设置 `REACT_APP_RESEARCH_WORKBENCH_URL`。

### 24h 多 Agent 投研任务中心
- **任务队列**: 投资研究、组合复盘、风险审查、观察名单监控任务统一进入队列。
- **常驻 worker**: 后端启动后自动运行 worker，持续拉取 `pending` 任务。
- **多 Agent 流水线**: OrchestratorAgent、DataSourceAgent、EvidenceAgent、ResearchAgent、SentimentAgent、ScenarioAgent、RiskAgent、ReportAgent 分阶段产出日志和报告。
- **状态持久化**: 任务、日志、结果保存到 `backend/.agent_tasks.sqlite3`，可重启后继续查看。
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
