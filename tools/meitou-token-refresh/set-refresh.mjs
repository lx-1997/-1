#!/usr/bin/env node
// 一次性配置 Cognito refresh token → 以后自动换票（不再需要浏览器登录）
//
//   printf '%s' "<refresh_token>" | ~/.meitou-refresh/bin/run.sh set-refresh
//
// 会把 refresh token 存进 ~/.meitou-refresh/config.json（600 权限），
// 立即换一张 access token 推到服务器并跑完整验证。
import { readFileSync, writeFileSync, chmodSync, existsSync } from "node:fs";
import path from "node:path";
import { loadConfig, log, RUNTIME_HOME, hoursLeft } from "./lib/config.mjs";
import { COGNITO_DEFAULTS, extractRefreshToken, refreshAccessToken } from "./lib/cognito.mjs";
import { applyHarvest } from "./lib/pipeline.mjs";
import { jwtExp } from "./lib/harvest.mjs";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8").trim();
}

const raw = await readStdin();
const refreshToken = extractRefreshToken(raw);
if (!refreshToken) {
  log("没识别出 refresh token。请在浏览器 Console 里执行：");
  log("  copy(Object.entries(localStorage).find(([k])=>/refresh.?token/i.test(k))?.[1] || 'NOT_FOUND')");
  process.exit(2);
}
log("收到 refresh token：长度", refreshToken.length, "（前缀", refreshToken.slice(0, 8) + "…）");

const cfg = loadConfig();
const cognito = { ...COGNITO_DEFAULTS, ...(cfg.cognito || {}), refreshToken };
log("向 Cognito 换票（region=" + cognito.region + " client=" + cognito.clientId + "）…");
let out;
try {
  out = await refreshAccessToken(cognito);
} catch (error) {
  log("换票失败：" + error.message);
  process.exit(3);
}
log("换票成功：access token 长度 %d，有效期 %.1f 小时", out.accessToken.length, out.expiresIn / 3600);

// 持久化（refresh token 可能轮换）
const cfgPath = path.join(RUNTIME_HOME, "config.json");
const saved = existsSync(cfgPath) ? JSON.parse(readFileSync(cfgPath, "utf8")) : {};
saved.cognito = { ...cognito, refreshToken: out.refreshToken || refreshToken };
writeFileSync(cfgPath, JSON.stringify(saved, null, 2) + "\n");
chmodSync(cfgPath, 0o600);
log("已写入 %s（600）", cfgPath);

const exp = jwtExp(out.accessToken);
const result = await applyHarvest(cfg, {
  token: out.accessToken, source: "cognito-refresh", exp, hoursLeft: hoursLeft(exp), validation: null,
}, { reason: "cognito-setup" });
log(result.ok ? "✅ 已接管：以后每 6 小时自动用 refresh token 换票，无需人工" : "❌ 未恢复：" + JSON.stringify(result));
process.exit(result.ok ? 0 : 2);
