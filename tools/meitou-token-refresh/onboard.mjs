#!/usr/bin/env node
// 一次性登录（onboarding）：开一个有头 Chrome 窗口，人工完成一次 Apple 登录，
// 脚本会自动把登录后的 Bearer token 推到服务器。之后定时任务即可无头续期。
//
//   ~/.meitou-refresh/bin/run.sh onboard
import { loadConfig, log, RUNTIME_HOME } from "./lib/config.mjs";
import { notify } from "./lib/alert.mjs";
import { harvestToken } from "./lib/harvest.mjs";
import { applyHarvest } from "./lib/pipeline.mjs";
import { ONBOARD_LOCK, REFRESH_LOCK, acquire, refreshRunning, release } from "./lib/lock.mjs";

const cfg = loadConfig();
const deadlineMinutes = Number(process.env.MEITOU_ONBOARD_MINUTES || 10);

// 定时续期正在用同一个 Chrome profile 时先等它跑完（通常 1-3 分钟）
if (refreshRunning()) {
  log("定时续期正在使用 Chrome profile，最多等待 3 分钟…");
  const until = Date.now() + 3 * 60 * 1000;
  while (refreshRunning() && Date.now() < until) {
    await new Promise((r) => setTimeout(r, 5000));
  }
  if (refreshRunning()) {
    log("仍在续期中，请稍后重试 onboard");
    process.exit(1);
  }
  log("profile 已空闲，继续登录向导");
}
acquire(ONBOARD_LOCK);
process.on("exit", () => release(ONBOARD_LOCK));

log("=".repeat(72));
log("美投镜像 token 登录向导");
log("1) 马上会弹出一个 Chrome 窗口（专用 profile，不影响你日常浏览器）");
log(`2) 请在 ${deadlineMinutes} 分钟内完成 登录（Apple 登录 + 双重认证）`);
log("3) 登录后会停在「美投圈/答疑」页面，脚本自动抓取并推送 token");
log(`profile 目录: ${RUNTIME_HOME}/chrome-profile`);
log("=".repeat(72));

const harvest = await harvestToken(cfg, {
  headless: false,
  deadlineMs: deadlineMinutes * 60 * 1000,
  verbose: true,
  nudge: false,   // 交互式登录：绝不导航，避免打断 Apple 登录弹窗
});

if (!harvest.token) {
  log("没抓到 token：可能没登录成功或超时");
  await notify("美投 token 登录向导未拿到 token", "请重新运行 ~/.meitou-refresh/bin/run.sh onboard", { cfg });
  process.exit(2);
}

const result = await applyHarvest(cfg, harvest, { reason: "onboard" });
log(result.ok ? "✅ 登录并推送成功" : "❌ 推送后仍不健康：" + JSON.stringify(result));
process.exit(result.ok ? 0 : 2);
