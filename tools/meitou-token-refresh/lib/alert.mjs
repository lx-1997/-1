// 告警：macOS 本地通知 + 可插拔 webhook（Server酱 / Bark / 企业微信 / 通用）
import { execFile } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { RUNTIME_HOME, loadConfig } from "./config.mjs";

const pexec = promisify(execFile);

function osascriptNotify(title, body) {
  const script =
    "display notification " + JSON.stringify(String(body).slice(0, 400)) +
    " with title " + JSON.stringify(String(title).slice(0, 120)) +
    ' sound name "Basso"';
  return pexec("osascript", ["-e", script]);
}

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10000),
  });
  return res.status;
}

export async function notify(title, body, { cfg = loadConfig() } = {}) {
  const logsDir = path.join(RUNTIME_HOME, "logs");
  mkdirSync(logsDir, { recursive: true });
  appendFileSync(path.join(logsDir, "alert.log"), `[${new Date().toISOString()}] ${title} :: ${body}\n`);

  const results = [];
  if (cfg.alerts.macos) {
    try {
      await osascriptNotify(title, body);
      results.push("macos:ok");
    } catch (error) {
      results.push("macos:" + error.message);
    }
  }
  if (cfg.alerts.serverchanKey) {
    try {
      const url = `https://sctapi.ftqq.com/${cfg.alerts.serverchanKey}.send`;
      const res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ title, desp: body }),
        signal: AbortSignal.timeout(10000),
      });
      results.push("serverchan:" + res.status);
    } catch (error) {
      results.push("serverchan:" + error.message);
    }
  }
  if (cfg.alerts.barkUrl) {
    try {
      const base = cfg.alerts.barkUrl.replace(/\/$/, "");
      const status = await postJson(`${base}/${encodeURIComponent(title)}/${encodeURIComponent(body)}`, {});
      results.push("bark:" + status);
    } catch (error) {
      results.push("bark:" + error.message);
    }
  }
  if (cfg.alerts.wecomWebhook) {
    try {
      const status = await postJson(cfg.alerts.wecomWebhook, { msgtype: "text", text: { content: `${title}\n${body}` } });
      results.push("wecom:" + status);
    } catch (error) {
      results.push("wecom:" + error.message);
    }
  }
  if (cfg.alerts.genericWebhook) {
    try {
      const status = await postJson(cfg.alerts.genericWebhook, { title, body, at: new Date().toISOString() });
      results.push("generic:" + status);
    } catch (error) {
      results.push("generic:" + error.message);
    }
  }
  return results;
}
