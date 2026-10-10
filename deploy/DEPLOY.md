# DeepFocus Web 终端 — VPS 部署指南

把这个金融终端（React 前端 + FastAPI 后端，数据全用公开源）部署到你自己的云服务器，
单域名 + 自动 HTTPS，一条命令起。

## 架构

```
浏览器 ──HTTPS──> Caddy(:80/:443，自动证书)
                    ├─ /api/*, /health  ─> backend  (uvicorn :8300)
                    └─ 其它             ─> frontend (nginx 静态)
```

四个容器：`frontend`(静态页) / `backend`(FastAPI) / `research-workbench`(Node + Chromium) / `caddy`(反代+TLS)。工作台仅暴露于容器内部网络。

## 前置条件

1. 一台 Linux 云服务器（2C2G 起步够用），装好 **Docker** 和 **Docker Compose v2**。
2. 一个域名，DNS **A 记录**指向服务器公网 IP。
3. 安全组/防火墙放行 **80 / 443**。

## 部署步骤

```bash
# 1. 把项目拷到服务器(git clone 或 scp 整个 -1-main 目录)
cd -1-main/deploy

# 2. 配置
cp .env.example .env
vi .env          # 改 DOMAIN、PUBLIC_URL、DEEPFOCUS_JWT_SECRET

# 3. 构建并启动
docker compose up -d --build

# 4. 看状态 / 日志
docker compose ps
docker compose logs -f
```

完成后浏览器打开 `https://你的域名` 即可。首次访问 Caddy 会自动申请证书（几十秒）。

## 更新

```bash
git pull            # 或重新上传代码
docker compose up -d --build
```

## 数据与持久化

- 所有后端运行时存储通过 `DEEPFOCUS_DATA_DIR=/data` 写入卷 `backend_data`。专用数据库路径环境变量优先；升级旧部署时先备份原文件并迁移到 `/data`，不要仅改路径。
- 研报下载、任务记录、导出与浏览器登录状态分别保存在工作台持久卷中；模型配置通过 `/data/.model_config.json` 与后端共享。
- 后端采用一个 API worker；量化结果落库保留 1 小时。重启前未完成的量化任务标记为中断，由用户重新提交，不自动重复调用模型。
- 发布前执行 `bash scripts/check-container-delivery.sh`，验证干净启动、公开路由、工作台和容器重建后的 SQLite 持久化。
- 行情/新闻等**公开数据源开箱即用**，不配任何 key 也能跑。
- 想要更高额度/更多覆盖，在 `.env` 里填免费 key（Tushare / Finnhub / AlphaVantage）。
- 想接真实大模型分析，在 `.env` 配 `DEEPFOCUS_LLM_PROVIDER=openai` + `OPENAI_API_KEY`（默认 mock 不影响行情/新闻）。

## 做成「对外产品」的安全建议

- **务必**把 `DEEPFOCUS_JWT_SECRET` 改成长随机串。
- 私有/内部用：设 `DEEPFOCUS_AUTH_REQUIRED=true`，并把 `DEEPFOCUS_ALLOW_SELF_REGISTER=false`，用管理员账号 (`DEEPFOCUS_ADMIN_EMAIL/PASSWORD`) 控制。
- 上量后建议把账号库从 SQLite 换 Postgres：设 `DEEPFOCUS_DATABASE_URL`（见 `.env.example` 注释）。

## 改名/品牌(让它更像你的产品)

- 站点标题/图标：`public/index.html`、`public/logo*.png`、`public/manifest.json`。
- 应用内名称/文案：`src/` 下组件 + `src/i18n`。
- 这是你自己的代码库（DeepFocus / trading-client），随意改，不涉及任何第三方授权。

## 常见问题

- **证书签发失败**：确认域名已解析到本机、80/443 已放行；`docker compose logs caddy` 看详情。
- **前端调不到后端**：`PUBLIC_URL` 必须等于对外访问的地址（改完要 `--build` 重新打包前端，API 地址是编译期写入的）。
- **本地试跑**：`.env` 里 `DOMAIN=localhost`、`PUBLIC_URL=https://localhost`，然后 `docker compose up --build`，浏览器开 `https://localhost`（自签证书需手动信任）。
