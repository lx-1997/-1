# 研报数据与 AI 对话工作台

这是当前工具整理出的独立项目目录，包含：

- 知识星球研报抓取与选中文件下载
- 文件库与模糊搜索
- ChatGPT 风格对话工作台
- 文件引用、解析研报 Skill、文件问答 Skill
- OpenAI-compatible 模型配置

## 启动

```bash
npm install
ZSXQ_COOKIE="你的知识星球 Cookie" ZSXQ_ADUID="你的 X-Aduid" npm run tool
```

默认地址：

```text
http://127.0.0.1:3927
```

## 目录

```text
tool-server.js       # 本地 API 服务
zsxq-downloader.js   # 知识星球抓取下载 CLI
tool-public/         # 前端页面
downloads/           # 下载文件目录
.tool-runs/          # 临时任务文件目录
```

## 说明

这个目录不包含 `node_modules`、登录 Cookie、API Key，也不包含已下载研报文件。复制到其它位置后执行 `npm install` 即可恢复依赖。

