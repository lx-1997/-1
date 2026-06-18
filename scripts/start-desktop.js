#!/usr/bin/env node
/**
 * Smart desktop dev startup.
 * Starts the backend and React renderer when needed, waits until they are reachable,
 * then launches Electron with the correct renderer URL.
 */
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const rootDir = path.join(__dirname, '..');
const host = process.env.HOST || '0.0.0.0';
const port = Number(process.env.PORT || 3000);
const rendererUrl = process.env.ELECTRON_RENDERER_URL || `http://${host}:${port}`;
const apiBaseUrl = (process.env.REACT_APP_API_BASE_URL || 'http://127.0.0.1:8300').replace(/\/$/, '');
const apiHealthUrl = `${apiBaseUrl}/health`;
const backendScript = process.env.DESKTOP_BACKEND_SCRIPT || 'backend:long';
const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm';

function checkUrl(url) {
  return new Promise(resolve => {
    const request = http.get(url, response => {
      response.resume();
      resolve(response.statusCode && response.statusCode < 500);
    });
    request.on('error', () => resolve(false));
    request.setTimeout(1500, () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForRenderer(timeoutMs = 90000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await checkUrl(rendererUrl)) {
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return false;
}

async function waitForBackend(timeoutMs = 90000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await checkUrl(apiHealthUrl)) {
      return true;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return false;
}

function startBackend() {
  console.log(`[desktop-dev] Starting backend via npm run ${backendScript} at ${apiBaseUrl}`);
  return spawn(npmBin, ['run', backendScript], {
    cwd: rootDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      REACT_APP_API_BASE_URL: apiBaseUrl
    }
  });
}

function startRenderer() {
  console.log(`[desktop-dev] Starting React renderer at ${rendererUrl}`);
  return spawn(npmBin, ['run', 'start'], {
    cwd: rootDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      BROWSER: 'none',
      HOST: host,
      PORT: String(port),
      REACT_APP_API_BASE_URL: apiBaseUrl
    }
  });
}

function startElectron() {
  const electronBin = require('electron');
  console.log(`[desktop-dev] Launching Electron with ${rendererUrl}`);
  return spawn(electronBin, [rootDir], {
    cwd: rootDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      ELECTRON_IS_DEV: '1',
      ELECTRON_RENDERER_URL: rendererUrl
    }
  });
}

async function main() {
  let backendProcess = null;
  if (await checkUrl(apiHealthUrl)) {
    console.log(`[desktop-dev] Reusing existing backend at ${apiBaseUrl}`);
  } else {
    backendProcess = startBackend();
    if (!(await waitForBackend())) {
      console.error(`[desktop-dev] Backend did not become ready: ${apiHealthUrl}`);
      backendProcess.kill();
      process.exit(1);
    }
  }

  let rendererProcess = null;
  if (await checkUrl(rendererUrl)) {
    console.log(`[desktop-dev] Reusing existing renderer at ${rendererUrl}`);
  } else {
    rendererProcess = startRenderer();
    if (!(await waitForRenderer())) {
      console.error(`[desktop-dev] Renderer did not become ready: ${rendererUrl}`);
      rendererProcess.kill();
      process.exit(1);
    }
  }

  const electronProcess = startElectron();
  const shutdown = () => {
    electronProcess.kill();
    rendererProcess?.kill();
    backendProcess?.kill();
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  electronProcess.on('close', code => {
    rendererProcess?.kill();
    backendProcess?.kill();
    process.exit(code || 0);
  });
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
