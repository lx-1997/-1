#!/usr/bin/env node
// 定时入口：服务器上的 token 快过期时，用本机持久化 Chrome 会话换一张新票并推送。
//
//   node refresh.mjs            # 正常巡检（token 还够用就直接跳过）
//   node refresh.mjs --force    # 无视剩余时间，强制采集
import { loadConfig, log, RUNTIME_HOME, hoursLeft } from "./lib/config.mjs";
import { notify } from "./lib/alert.mjs";
import { harvestToken } from "./lib/harvest.mjs";
import { applyHarvest } from "./lib/pipeline.mjs";
import { remoteTokenStatus } from "./lib/server.mjs";
import { writeState } from "./lib/state.mjs";
import { ONBOARD_LOCK, REFRESH_LOCK, acquire, onboardRunning, pidAlive, refreshRunning, release } from "./lib/lock.mjs";
import { readFileSync } from "node:fs";

const force = process.argv.includes("--force");
const cfg = loadConfig();

// 登录向导进行中不要抢 profile
if (onboardRunning()) {
  log("检测到登录向导正在运行（" + readFileSync(ONBOARD_LOCK, "utf8").trim() + "），本轮跳过");
  process.exit(0);
}
if (refreshRunning()) {
  log("已有一轮续期在运行，本轮跳过");
  process.exit(0);
}
acquire(REFRESH_LOCK);
process.on("exit", () => release(REFRESH_LOCK));

try {
  const status = await remoteTokenStatus(cfg);
  log("服务器 token:", JSON.stringify({ present: status.present, hoursLeft: status.hoursLeft, length: status.length, exp: status.exp }));
  if (!force && status.hoursLeft !== null && status.hoursLeft > cfg.minRemainingHours) {
    log(`剩余 ${status.hoursLeft}h > ${cfg.minRemainingHours}h，本轮跳过采集`);
    writeState({ lastCheckAt: new Date().toISOString(), lastSkipReason: "token-fresh", lastSkipHoursLeft: status.hoursLeft });
    process.exit(0);
  }

  // 优先走 Cognito refresh token（无需浏览器）；没有配置或无效应答再回退浏览器采集
  let harvest = null;
  try {
    const { cognitoAccessToken } = await import("./lib/cognito.mjs");
    const { jwtExp } = await import("./lib/harvest.mjs");
    const out = await cognitoAccessToken(cfg);
    if (out) {
      const exp = jwtExp(out.accessToken);
      harvest = { token: out.accessToken, source: "cognito-refresh", exp, hoursLeft: hoursLeft(exp), validation: null };
      // 轮换后的 refresh token 回写
      if (out.refreshToken && out.refreshToken !== (cfg.cognito || {}).refreshToken) {
        const { readFileSync, writeFileSync, chmodSync } = await import("node:fs");
        const path = await import("node:path");
        const cfgPath = path.join(RUNTIME_HOME, "config.json");
        const saved = JSON.parse(readFileSync(cfgPath, "utf8"));
        saved.cognito = { ...(saved.cognito || {}), refreshToken: out.refreshToken };
        writeFileSync(cfgPath, JSON.stringify(saved, null, 2) + "\n");
        chmodSync(cfgPath, 0o600);
        log("Cognito refresh token 已轮换并回写");
      }
    }
  } catch (error) {
    log("Cognito 换票不可用（回退浏览器采集）：" + error.message);
  }
  if (!harvest) harvest = await harvestToken(cfg, { headless: true, deadlineMs: 90000 });
  const result = await applyHarvest(cfg, harvest, { reason: force ? "force" : "scheduled" });
  process.exit(result.ok ? 0 : 2);
} catch (error) {
  log("刷新失败:", error.stack || error.message);
  await notify("美投 token 刷新脚本异常", String(error.message || error).slice(0, 300), { cfg });
  writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "exception", detail: String(error.message || error) } });
  process.exit(3);
}
