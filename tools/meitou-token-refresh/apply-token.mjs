#!/usr/bin/env node
// 手工粘贴 token 入口：从 stdin 读一条 Bearer token，验证后半自动接管
// （推送服务器 → token-check 复核 → 触发完整同步 → 轮询确认恢复）
//
//   printf '%s' "<auth_token>" | ~/.meitou-refresh/bin/run.sh apply-token
import { loadConfig, log, hoursLeft } from "./lib/config.mjs";
import { applyHarvest } from "./lib/pipeline.mjs";
import { jwtExp } from "./lib/harvest.mjs";

const cfg = loadConfig();

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8").trim();
}

const token = await readStdin();
if (!token) {
  log("没有从 stdin 读到 token。用法：printf '%s' \"<auth_token>\" | run.sh apply-token");
  process.exit(2);
}
const exp = jwtExp(token);
log("收到 token：长度", token.length, "| exp", exp ? new Date(exp * 1000).toISOString() : "解析失败", "| 剩余", hoursLeft(exp), "h");
if (!exp) {
  log("这不是一条可解析的 JWT，请确认复制的是 localStorage 里的 auth_token 完整值。");
  process.exit(2);
}
const result = await applyHarvest(cfg, { token, source: "manual-paste", exp, hoursLeft: hoursLeft(exp), validation: null }, { reason: "manual-token" });
log(result.ok ? "✅ 已接管：镜像应已恢复授权同步" : "❌ 未恢复：" + JSON.stringify(result));
process.exit(result.ok ? 0 : 2);
