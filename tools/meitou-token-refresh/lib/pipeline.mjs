// 采集结果 → 推送服务器 → 服务器侧校验 → 触发同步 → 端到端确认恢复
import { log } from "./config.mjs";
import { notify } from "./alert.mjs";
import { pushToken, remoteTokenCheck, remoteTokenStatus, triggerSync, waitForHealthy, mirrorStatus } from "./server.mjs";
import { writeState } from "./state.mjs";

function describe(harvest) {
  const v = harvest.validation || {};
  return (
    `token=${harvest.token ? harvest.token.length + "字符" : "无"} ` +
    `来源=${harvest.source || "-"} ` +
    `剩余=${harvest.hoursLeft ?? "-"}h ` +
    `探针=${v.status || "-"}/帖子${v.posts ?? "-"}/带明细${v.postsWithTransactions ?? "-"}`
  );
}

export async function applyHarvest(cfg, harvest, { reason = "manual" } = {}) {
  log("采集结果:", describe(harvest));

  if (!harvest.token) {
    await notify("美投 token 需要重新登录", `浏览器 profile 里没有可用 token（${reason}）。请运行：~/.meitou-refresh/bin/run.sh onboard`, { cfg });
    writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "no-token", detail: describe(harvest) } });
    return { ok: false, reason: "no-token" };
  }
  const before = await mirrorStatus(cfg).catch(() => null);
  const v = harvest.validation || {};
  // 手工粘贴的 token 没有浏览器探针结果；此时以服务器侧 token-check 为准
  if (!["manual-paste", "cognito-refresh"].includes(harvest.source) && (v.status !== 200 || !v.posts)) {
    await notify("美投 token 校验未通过", `${describe(harvest)}；error=${v.error || ""}。可能需要重新登录。`, { cfg });
    writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "probe-failed", detail: describe(harvest) } });
    return { ok: false, reason: "probe-failed", harvest };
  }
  if (!v.postsWithTransactions) {
    log("提示: 探针第一页没有交易明细，可能是这批帖子本身就没明细；以服务器侧校验为准");
  }

  const pushed = await pushToken(cfg, harvest.token);
  log("已写入服务器:", JSON.stringify(pushed));
  const after = await remoteTokenStatus(cfg);
  log("服务器 token 现状:", JSON.stringify({ exp: after.exp, hoursLeft: after.hoursLeft, length: after.length }));
  if (after.exp !== harvest.exp) {
    await notify("美投 token 写入异常", `服务器上的 token exp=${after.exp} 与采集到的 exp=${harvest.exp} 不一致`, { cfg });
    writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "push-mismatch" } });
    return { ok: false, reason: "push-mismatch" };
  }

  const check = await remoteTokenCheck(cfg).catch((error) => ({ exitCode: null, output: error.message }));
  log("服务器 token-check 退出码:", check.exitCode, "|", check.output.split("\n").slice(-2).join(" / "));
  if (check.exitCode !== 0) {
    await notify("美投 token 服务器侧校验失败", `token-check 退出码 ${check.exitCode}\n${check.output.slice(-300)}`, { cfg });
    writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "server-check-failed", exitCode: check.exitCode } });
    return { ok: false, reason: "server-check-failed", check };
  }

  log("触发一次完整同步…");
  const sync = await triggerSync(cfg).catch((error) => ({ exitCode: null, raw: error.message }));
  log("同步退出码:", sync.exitCode, "|", (sync.raw || "").slice(-200));

  const healthy = await waitForHealthy(cfg);
  const ok = Boolean(healthy && healthy.authExpired === false && healthy.status === "ok");
  log("端到端状态:", JSON.stringify(healthy));

  if (ok) {
    if (before && (before.authExpired || before.status !== "ok")) {
      await notify("美投镜像已恢复授权同步", `会员内容同步恢复（${reason}）。服务器 token 剩余 ${after.hoursLeft}h。`, { cfg });
    }
    writeState({
      lastApply: { at: new Date().toISOString(), ok: true, reason, detail: describe(harvest) },
      lastSuccessAt: new Date().toISOString(),
      lastSuccessExp: after.exp,
    });
    return { ok: true, healthy };
  }

  await notify("美投镜像授权同步未恢复", `已写入新 token 但快照仍是 ${healthy?.status}/${healthy?.authExpired}：${healthy?.warning || healthy?.error || ""}`, { cfg });
  writeState({ lastApply: { at: new Date().toISOString(), ok: false, reason: "sync-not-healthy", detail: JSON.stringify(healthy) } });
  return { ok: false, reason: "sync-not-healthy", healthy };
}
