// 简易进程锁：避免定时续期与登录向导、或多轮续期之间抢同一个 Chrome profile
import { existsSync, readFileSync, writeFileSync, unlinkSync, mkdirSync } from "node:fs";
import path from "node:path";
import { RUNTIME_HOME } from "./config.mjs";

export const ONBOARD_LOCK = path.join(RUNTIME_HOME, "onboard.lock");
export const REFRESH_LOCK = path.join(RUNTIME_HOME, "refresh.lock");

export function pidAlive(pid) {
  if (!pid || Number.isNaN(pid)) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function lockHolder(file) {
  try {
    return Number(readFileSync(file, "utf8").trim());
  } catch {
    return 0;
  }
}

export function onboardRunning() {
  return existsSync(ONBOARD_LOCK) && pidAlive(lockHolder(ONBOARD_LOCK));
}

export function refreshRunning() {
  return existsSync(REFRESH_LOCK) && pidAlive(lockHolder(REFRESH_LOCK));
}

export function acquire(file) {
  mkdirSync(RUNTIME_HOME, { recursive: true });
  writeFileSync(file, String(process.pid) + "\n");
}

export function release(file) {
  try {
    if (lockHolder(file) === process.pid) unlinkSync(file);
  } catch { /* 忽略 */ }
}
