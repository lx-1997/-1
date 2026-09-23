#!/usr/bin/env node
// 巡检：读镜像站公开快照，判断授权同步是否降级；降级即告警，并尝试自动续期一次。
//
//   ~/.meitou-refresh/bin/run.sh monitor
import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig, log, RUNTIME_HOME } from "./lib/config.mjs";
import { notify } from "./lib/alert.mjs";
import { mirrorStatus, remoteTokenStatus } from "./lib/server.mjs";
import { readState, writeState } from "./lib/state.mjs";

const cfg = loadConfig();
const ALERT_REPEAT_MS = 6 * 60 * 60 * 1000;   // 持续降级时最多每 6h 提醒一次
const STALE_MINUTES = 45;                     // 快照超过这么久没更新视为同步卡住
const AUTOFIX_COOLDOWN_MS = 30 * 60 * 1000;

const state = readState();
const status = await mirrorStatus(cfg);
const degraded = status.authExpired || status.status !== "ok";
const stale = status.ageMinutes !== null && status.ageMinutes > STALE_MINUTES;

log("快照:", JSON.stringify(status));
log("判定:", JSON.stringify({ degraded, stale, degradedChecks: state.degradedChecks || 0 }));

const now = Date.now();
const patch = {
  lastMonitorAt: new Date().toISOString(),
  lastSnapshotAt: status.updatedAt,
  lastSnapshotStatus: status.status,
  lastAuthExpired: status.authExpired,
  degradedChecks: degraded ? (state.degradedChecks || 0) + 1 : 0,
};

if (!degraded && !stale) {
  if (state.wasDegraded) {
    await notify("美投镜像同步已恢复", `快照状态 ${status.status}，updatedAt=${status.updatedAt}`, { cfg });
  }
  writeState({ ...patch, wasDegraded: false, lastHealthyAt: new Date().toISOString() });
  log("状态正常");
  process.exit(0);
}

// 连续两次（≈1h）确认降级后才告警，避免抖动
const firstDetection = (state.degradedChecks || 0) === 0 && degraded;
const shouldAlert = (patch.degradedChecks >= 2 || stale) && (!state.lastAlertAt || now - Date.parse(state.lastAlertAt) > ALERT_REPEAT_MS);
if (shouldAlert && !firstDetection) {
  const tokenState = await remoteTokenStatus(cfg).catch((error) => ({ error: error.message }));
  const detail = degraded
    ? `授权同步降级：${status.warning || "authExpired=true"}；服务器 token 剩余 ${tokenState.hoursLeft ?? "?"}h（${tokenState.error || "ok"}）`
    : `同步疑似卡住：快照 ${status.ageMinutes} 分钟未更新（阈值 ${STALE_MINUTES}）`;
  await notify("美投镜像会员内容未同步", detail, { cfg });
  patch.lastAlertAt = new Date().toISOString();
  log("已告警:", detail);
}

// 自动续期：token 过期或快过期时，跑一次强制刷新（半小时冷却）
let autofix = "skipped";
if (cfg.autoHeal && degraded) {
  const tokenState = await remoteTokenStatus(cfg).catch(() => null);
  const expiredOrTight = !tokenState || tokenState.hoursLeft === null || tokenState.hoursLeft < cfg.minRemainingHours;
  const cooledDown = !state.lastAutoFixAt || now - Date.parse(state.lastAutoFixAt) > AUTOFIX_COOLDOWN_MS;
  if (expiredOrTight && cooledDown) {
    autofix = "triggered";
    patch.lastAutoFixAt = new Date().toISOString();
    const here = path.dirname(fileURLToPath(import.meta.url));
    log("触发自动续期 refresh.mjs --force");
    execFile(process.execPath, [path.join(here, "refresh.mjs"), "--force"], { timeout: 10 * 60 * 1000 }, (error, stdout, stderr) => {
      log("自动续期结束:", error ? "exit=" + error.code : "ok", (stdout || "").trim().split("\n").slice(-3).join(" | "), (stderr || "").trim().slice(-200));
    });
  } else {
    autofix = expiredOrTight ? "cooldown" : "token-still-fresh";
  }
}
patch.autofix = autofix;

writeState({ ...patch, wasDegraded: true });
process.exit(degraded ? 1 : 0);
