// Cognito refresh-token 换票：美投站点的登录态由 AWS Cognito 托管
// （iss = cognito-idp.us-east-2.amazonaws.com/us-east-2_fnA5JNchH）。
// 只要拿到一次 refreshToken，就能在它有效期内（默认 30 天）反复换取新的 access token，
// 完全不需要浏览器/人工登录 → 这是本工具链最省事的一条续期路径。
import { log } from "./config.mjs";

export const COGNITO_DEFAULTS = {
  region: "us-east-2",
  clientId: "nhlt84q4sndhj5bcjq65tsg2r",
  userPoolId: "us-east-2_fnA5JNchH",
};

/** 用 refresh token 换新的 access/id token。返回 {accessToken, idToken, refreshToken, expiresIn}。 */
export async function refreshAccessToken({ region, clientId, refreshToken }) {
  const url = `https://cognito-idp.${region}.amazonaws.com/`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/x-amz-json-1.1",
      "x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth",
    },
    body: JSON.stringify({
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: clientId,
      AuthParameters: { REFRESH_TOKEN: refreshToken },
    }),
    signal: AbortSignal.timeout(20000),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* 保留原文 */ }
  if (!res.ok) {
    const type = json?.__type || "";
    const message = json?.message || text.slice(0, 160);
    throw new Error(`Cognito 换票失败（${res.status} ${type}）：${message}`);
  }
  const auth = json?.AuthenticationResult || {};
  if (!auth.AccessToken) throw new Error("Cognito 返回里没有 AccessToken");
  return {
    accessToken: auth.AccessToken,
    idToken: auth.IdToken || "",
    refreshToken: auth.RefreshToken || "",   // 有时会轮换，需要回写
    expiresIn: Number(auth.ExpiresIn) || 0,
  };
}

/** 从用户粘贴的内容里提取 refresh token：支持纯 token、JSON、整段 localStorage dump。 */
export function extractRefreshToken(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  // 1) JSON（可能带 Cognito 前缀键）
  try {
    const obj = JSON.parse(text);
    const flat = typeof obj === "object" && obj ? Object.entries(obj) : [];
    for (const [k, v] of flat) {
      if (/refresh.?token/i.test(k) && typeof v === "string" && v.length > 20) return v.trim();
    }
  } catch { /* 不是 JSON，继续 */ }
  // 2) 形如 CognitoRefreshToken=xxx 或 "refreshToken":"xxx"
  const m = text.match(/refresh.?token["'\s:=]+([A-Za-z0-9._\-]{40,})/i);
  if (m) return m[1];
  // 3) 裸 token（Cognito refresh token 很长且无点号）
  if (/^[A-Za-z0-9._\-]{60,}$/.test(text)) return text;
  return "";
}

export async function cognitoAccessToken(cfg, { verbose = true } = {}) {
  const cognito = { ...COGNITO_DEFAULTS, ...(cfg.cognito || {}) };
  if (!cognito.refreshToken) return null;
  const out = await refreshAccessToken(cognito);
  if (verbose) {
    log(`Cognito 换票成功：新 access token 长度 ${out.accessToken.length}，有效期 ${(out.expiresIn / 3600).toFixed(1)}h`);
  }
  return { ...out, cognito };
}
