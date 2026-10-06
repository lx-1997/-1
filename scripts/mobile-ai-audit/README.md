# 移动端 AI 问答视口审计/验证脚本集

Playwright（`channel: 'chrome'`）+ 390×844 移动视口，直打线上站对移动端 AI 问答做
视觉审计与布局验证。从 `tmp/mobile-ai-audit/` 转正。运行：`node <脚本>`（仓库根或本目录均可，
依赖仓库 node_modules 里的 playwright）。

## 主审计

- `audit.js` — 全流程：欢迎页 → 点底部 AI tab → 建议 → 提问 → busy → 回答 → 追问，
  每步截图到 `shots/`，输出 composer/chatBody/nav 等关键布局指标与控制台错误。
- `probe.js` — 网络探针：只抓发送提问后的 `/api/*` 请求与状态码（定位 401/403 归属）。
- `welcome.js` — 单页回归：AI tab 欢迎态截图（发布后快速冒烟）。

## 键盘适配（--df-vvh / df-kb-open 机制）

- `kbsim.js` — 注入补丁 CSS + 模拟键盘（vvh=500），验证 composer 留在键盘上方。
- `livecheck.js` — **真实部署验证**（不注入 CSS）：欢迎态隐藏层核对 + 键盘态 composer 位置。
- `varprobe.js` — 只设 `--df-vvh`/`body.df-kb-open`，量容器/工作区 computed 高度，
  用于排查 CSS 竞争规则（flex 拉伸 vs height、后序媒体块覆盖）。
- `vvhtest.js` / `shellfix.js` — 壳层高度专项调试（历史保留）。

## 改后效果模拟

- `patched.js` / `placeholder.js` — 用 `addStyleTag`/改 DOM 属性在线上预览「未发布的改动」，
  发布前对比用。坑：`--df-vvh` 写入必须带 px 单位，无单位会让整条 var() 声明失效回落 auto。

## 其它会话产物（移动端对话形态批次）

`anscard.js` / `chatapp.js` / `density.js` / `domchain.js` / `h1probe.js` / `inspect2.js` /
`overflow.js` — 对话形态重构批次的专项探针（气泡/密度/溢出/DOM 链），按需取用。
