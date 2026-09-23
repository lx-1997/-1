#!/usr/bin/env node
/**
 * Build the web bundle used by the installable clients.
 *
 * The renderer is bundled into Electron/Capacitor, while API traffic stays on
 * the public daocaijing.com origin. Keeping this in a Node script makes the
 * command work from npm on macOS, Windows and Linux without shell-specific
 * environment-variable syntax.
 */
const path = require('path');
const { spawnSync } = require('child_process');

const target = process.argv[2];
if (!['desktop', 'android'].includes(target)) {
  console.error('Usage: node scripts/build-target.js <desktop|android>');
  process.exit(1);
}

const rootDir = path.join(__dirname, '..');
const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const apiBaseUrl = (process.env.DAOCJING_API_BASE_URL || process.env.REACT_APP_API_BASE_URL || 'https://daocaijing.com').replace(/\/$/, '');
const isTrue = value => typeof value === 'string' && value.trim().toLowerCase() === 'true';

// Packaged clients must never carry the demo credentials or auth bypass, even
// if a developer's shell/.env accidentally exports those flags. Use the plain
// `npm run build` command for a local demo bundle instead.
if (isTrue(process.env.REACT_APP_AUTH_BYPASS) || isTrue(process.env.REACT_APP_DEMO_LOGIN)) {
  console.error('[build-target] Refusing packaged build with REACT_APP_AUTH_BYPASS/REACT_APP_DEMO_LOGIN enabled');
  process.exit(1);
}

if (target === 'android' && !/^https:\/\//i.test(apiBaseUrl)) {
  console.error(`[build-target] Android builds require an HTTPS API base URL: ${apiBaseUrl}`);
  process.exit(1);
}

const result = spawnSync(npmBin, ['exec', '--', 'react-scripts', 'build'], {
  cwd: rootDir,
  stdio: 'inherit',
  env: {
    ...process.env,
    REACT_APP_API_BASE_URL: apiBaseUrl,
    // CRA already emits relative assets via homepage, this also protects a
    // caller that overrides homepage in a local .env file.
    PUBLIC_URL: '.'
  }
});

if (result.status !== 0) {
  process.exit(result.status || 1);
}

if (target === 'android') {
  // Debug APK 默认使用打包进 APK 的本地资源，这样原生插件（后台服务、通知点击）
  // 与当前前端版本一起生效；若要联调线上网页，可显式设置 CAPACITOR_SERVER_URL。
  const capacitorServerUrl = process.env.CAPACITOR_SERVER_URL?.trim();
  if (capacitorServerUrl && !/^https:\/\//i.test(capacitorServerUrl)) {
    console.error(`[build-target] Android remote WebView URL must use HTTPS: ${capacitorServerUrl}`);
    process.exit(1);
  }
  if (capacitorServerUrl) {
    let host;
    try { host = new URL(capacitorServerUrl).hostname.toLowerCase(); } catch {
      console.error(`[build-target] Invalid Android remote WebView URL: ${capacitorServerUrl}`);
      process.exit(1);
    }
    if (host !== 'daocaijing.com' && host !== 'www.daocaijing.com') {
      console.error(`[build-target] Android remote WebView URL must be daocaijing.com: ${capacitorServerUrl}`);
      process.exit(1);
    }
  }
  const syncEnv = { ...process.env };
  if (capacitorServerUrl) syncEnv.CAPACITOR_SERVER_URL = capacitorServerUrl;
  else delete syncEnv.CAPACITOR_SERVER_URL;
  const cap = spawnSync(npmBin, ['exec', '--', 'cap', 'sync', 'android'], {
    cwd: rootDir,
    stdio: 'inherit',
    env: syncEnv
  });
  if (cap.status !== 0) {
    process.exit(cap.status || 1);
  }
}

console.log(`[build-target] ${target} bundle ready; API base: ${apiBaseUrl}`);
