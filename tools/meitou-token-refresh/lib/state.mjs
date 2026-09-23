// 运行状态持久化（去重告警、记录最近一次续期结果）
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import path from "node:path";
import { RUNTIME_HOME } from "./config.mjs";

const STATE_FILE = path.join(RUNTIME_HOME, "state.json");

export function readState() {
  try {
    return JSON.parse(readFileSync(STATE_FILE, "utf8"));
  } catch {
    return {};
  }
}

export function writeState(patch) {
  const next = { ...readState(), ...patch, updatedAt: new Date().toISOString() };
  mkdirSync(RUNTIME_HOME, { recursive: true });
  writeFileSync(STATE_FILE, JSON.stringify(next, null, 2) + "\n");
  return next;
}

export function stateFileExists() {
  return existsSync(STATE_FILE);
}
