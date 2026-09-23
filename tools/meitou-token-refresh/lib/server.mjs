// 服务器侧操作：读取远端 token 状态 / 原子替换 token / 触发同步 / 读取镜像状态
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { hoursLeft } from "./config.mjs";

const pexec = promisify(execFile);

export async function ssh(cfg, remoteCommand, { input, timeoutMs = 60000 } = {}) {
  return pexec(
    "ssh",
    ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", cfg.server, remoteCommand],
    { input, timeout: timeoutMs, maxBuffer: 16 * 1024 * 1024 },
  );
}

const REMOTE_TOKEN_STATUS_PY = `
import json, re, pathlib, base64, time
p = pathlib.Path("__ENV_FILE__")
txt = p.read_text() if p.exists() else ""
m = re.search(r"^MEITOU_BEARER_TOKEN=(.*)$", txt, re.M)
tok = m.group(1).strip() if m else ""
def dec(t):
    try:
        pay = t.split(".")[1]
        pay += "=" * (-len(pay) % 4)
        return json.loads(base64.urlsafe_b64decode(pay))
    except Exception:
        return {}
pl = dec(tok)
exp = pl.get("exp") or 0
print(json.dumps({
    "present": bool(tok),
    "length": len(tok),
    "iat": pl.get("iat"),
    "exp": exp,
    "remainingHours": round((exp - time.time()) / 3600, 2) if exp else None,
    "mtime": int(p.stat().st_mtime) if p.exists() else 0,
}))
`;

export async function remoteTokenStatus(cfg, { timeoutMs = 30000 } = {}) {
  const script = REMOTE_TOKEN_STATUS_PY.replace("__ENV_FILE__", cfg.remoteEnvFile);
  const { stdout } = await ssh(cfg, `python3 - <<'PYEOF'\n${script}\nPYEOF`, { timeoutMs });
  const line = stdout.trim().split("\n").filter(Boolean).pop();
  const status = JSON.parse(line);
  status.hoursLeft = hoursLeft(status.exp);
  return status;
}

const REMOTE_PUSH_PY = `
import json, re, pathlib, shutil, time
tok = pathlib.Path("/tmp/meitou-token.new").read_text().strip()
if tok.count(".") != 2 or len(tok) < 200:
    raise SystemExit("token 形状不对，拒绝写入")
p = pathlib.Path("__ENV_FILE__")
txt = p.read_text()
backup = "/root/meitou-mirror.env.bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(p, backup)
if re.search(r"^MEITOU_BEARER_TOKEN=", txt, re.M):
    txt = re.sub(r"^MEITOU_BEARER_TOKEN=.*$", "MEITOU_BEARER_TOKEN=" + tok, txt, flags=re.M)
else:
    txt = txt.rstrip("\\n") + "\\nMEITOU_BEARER_TOKEN=" + tok + "\\n"
p.write_text(txt)
p.chmod(0o600)
print(json.dumps({"backup": backup, "length": len(tok), "envFile": str(p)}))
`;

export async function pushToken(cfg, token, { timeoutMs = 60000 } = {}) {
  // token 走 stdin 落到远端临时文件，避免出现在本机/远端的进程命令行里
  await ssh(cfg, "umask 077 && cat > /tmp/meitou-token.new", { input: token, timeoutMs });
  const script = REMOTE_PUSH_PY.replace("__ENV_FILE__", cfg.remoteEnvFile);
  const { stdout } = await ssh(cfg, `python3 - <<'PYEOF'\n${script}\nPYEOF`, { timeoutMs });
  await ssh(cfg, "rm -f /tmp/meitou-token.new", { timeoutMs: 20000 }).catch(() => {});
  return JSON.parse(stdout.trim().split("\n").filter(Boolean).pop());
}

// 服务器侧独立校验：走 gqlrealauth 确认这条 token 从服务器出口 IP 也可用
export async function remoteTokenCheck(cfg, { timeoutMs = 90000 } = {}) {
  const cmd =
    `set -a; . ${cfg.remoteEnvFile}; set +a; ` +
    `cd ${cfg.remoteBuildDir} && /usr/bin/node ${cfg.remoteBuildDir}/token-check.mjs; echo "check-exit=$?"`;
  const { stdout } = await ssh(cfg, cmd, { timeoutMs });
  const exitMatch = stdout.match(/check-exit=(\d+)/);
  return { exitCode: exitMatch ? Number(exitMatch[1]) : null, output: stdout.trim() };
}

export async function triggerSync(cfg, { timeoutMs = 15 * 60 * 1000 } = {}) {
  const cmd =
    `flock -n /var/run/meitou-mirror-sync.lock sh -c ` +
    `'set -a; . ${cfg.remoteEnvFile}; set +a; cd ${cfg.remoteMirrorDir} && /usr/bin/node ${cfg.remoteBuildDir}/sync.mjs' ` +
    `>> ${cfg.remoteSyncLog} 2>&1; echo "sync-exit=$?"`;
  const { stdout } = await ssh(cfg, cmd, { timeoutMs });
  const m = stdout.match(/sync-exit=(\d+)/);
  return { exitCode: m ? Number(m[1]) : null, raw: stdout.trim() };
}

export async function mirrorStatus(cfg, { timeoutMs = 30000 } = {}) {
  const url = cfg.mirrorStatusUrl + (cfg.mirrorStatusUrl.includes("?") ? "&" : "?") + "t=" + Date.now();
  const res = await fetch(url, { headers: { "user-agent": "meitou-token-refresh/1.0" }, signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error("镜像快照 HTTP " + res.status);
  const json = await res.json();
  const sync = json.sync || {};
  const ageMinutes = json.updatedAt ? Math.round((Date.now() - Date.parse(json.updatedAt)) / 60000) : null;
  return {
    updatedAt: json.updatedAt || null,
    ageMinutes,
    status: sync.status || "unknown",
    authExpired: Boolean(sync.authExpired),
    authenticated: Boolean(sync.authenticated),
    warning: sync.warning || "",
    trades: sync.trades || null,
    videoLists: sync.videoLists || null,
  };
}

export async function waitForHealthy(cfg, { attempts = 20, intervalMs = 20000 } = {}) {
  let last = null;
  for (let i = 0; i < attempts; i += 1) {
    try {
      last = await mirrorStatus(cfg);
      if (!last.authExpired && last.status === "ok") return last;
    } catch (error) {
      last = { error: error.message };
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return last;
}
