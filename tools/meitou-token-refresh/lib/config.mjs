// 美投镜像 Bearer token 自动续期 —— 配置与运行时常量
//
// 运行时目录默认 ~/.meitou-refresh（浏览器 profile、日志、config.json、state.json）。
// 可用 MEITOU_REFRESH_HOME 覆盖（测试用）。
import os from "node:os";
import path from "node:path";
import { readFileSync, existsSync } from "node:fs";

export const RUNTIME_HOME = process.env.MEITOU_REFRESH_HOME || path.join(os.homedir(), ".meitou-refresh");

const DEFAULTS = {
  // 生产服务器（镜像站 + 同步脚本所在机器）
  server: "root@39.105.214.141",
  remoteEnvFile: "/etc/meitou-mirror.env",
  remoteBuildDir: "/var/www/meitou-build",
  remoteMirrorDir: "/var/www/meitou-mirror",
  remoteSyncLog: "/var/log/meitou-mirror-sync-manual.log",

  // 镜像站公开状态快照（Mac 侧巡检用，不碰服务器）
  mirrorStatusUrl: "https://daocaijing.com/meitou/site-data-lite.json",

  // 源站
  sourceOrigin: "https://www.jdbinvesting.com",
  tokenProbePath: "/meitouquan/questions",
  authGqlUrl: "https://gql.jdbinvesting.com/gqlrealauth",
  localStorageKey: "auth_token",

  // 服务器上的 token 剩余时间高于此值就跳过浏览器采集（省资源）
  minRemainingHours: 8,
  // 采集到的 token 至少要剩这么久才算“新鲜”
  freshTokenHours: 6,
  // 巡检发现降级时是否自动触发一次续期
  autoHeal: true,

  chromeChannel: "chrome",

  alerts: {
    macos: true,
    // 任选其一填上即可（留空则不启用）
    serverchanKey: "",   // https://sct.ftqq.com 的 SendKey
    barkUrl: "",         // 如 https://api.day.app/xxxxxxxx
    wecomWebhook: "",    // 企业微信群机器人 webhook
    genericWebhook: "",  // 任意 POST {"title","body"} 的地址
  },
};

export function loadConfig() {
  const file = path.join(RUNTIME_HOME, "config.json");
  let user = {};
  if (existsSync(file)) {
    try {
      user = JSON.parse(readFileSync(file, "utf8"));
    } catch (error) {
      console.error("[config] config.json 解析失败，使用默认值:", error.message);
    }
  }
  return { ...DEFAULTS, ...user, alerts: { ...DEFAULTS.alerts, ...(user.alerts || {}) } };
}

export function log(...args) {
  console.log("[" + new Date().toISOString() + "]", ...args);
}

export function hoursLeft(expSeconds) {
  if (!expSeconds) return null;
  return Math.round(((expSeconds * 1000 - Date.now()) / 3600000) * 100) / 100;
}

export function isoFromSeconds(expSeconds) {
  return expSeconds ? new Date(expSeconds * 1000).toISOString() : null;
}
