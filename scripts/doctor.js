#!/usr/bin/env node
/**
 * Local project health checks for the DeepFocus desktop workspace.
 */
const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..');

function exists(relativePath) {
  return fs.existsSync(path.join(rootDir, relativePath));
}

function read(relativePath) {
  return fs.readFileSync(path.join(rootDir, relativePath), 'utf8');
}

const checks = [];

function add(name, ok, detail, level = 'error') {
  checks.push({ name, ok, detail, level });
}

const nodeMajor = Number(process.versions.node.split('.')[0]);
add('Node.js', nodeMajor >= 18, `current ${process.versions.node}; recommended >= 18`);
add('package-lock', exists('package-lock.json'), 'npm ci needs package-lock.json');
add('frontend dependencies', exists('node_modules/react-scripts'), 'run npm install when missing');
add('research workbench dependencies', exists('modules/research-workbench/node_modules'), 'postinstall should install nested workbench dependencies', 'warn');
add('backend virtualenv', exists('backend/.venv/bin/python') || exists('backend/.venv/Scripts/python.exe'), 'backend script expects backend/.venv', 'warn');
add('Android project', exists('android/gradlew'), 'Capacitor Android wrapper present', 'warn');

const electronSource = exists('public/electron.js') ? read('public/electron.js') : '';
add(
  'Electron isolation',
  /nodeIntegration:\s*false/.test(electronSource)
    && /contextIsolation:\s*true/.test(electronSource)
    && /enableRemoteModule:\s*false/.test(electronSource),
  'nodeIntegration=false, contextIsolation=true, remote=false'
);
add(
  'External navigation guard',
  /setWindowOpenHandler/.test(electronSource) && /will-navigate/.test(electronSource),
  'external links should leave the app shell safely'
);

const envText = exists('backend/.env') ? read('backend/.env') : '';
add(
  'Model config hint',
  /OPENAI_API_KEY|MINIMAX_API_KEY|MODEL_PROVIDER|DEEPFOCUS_MODEL_PROVIDER/.test(envText) || Boolean(process.env.OPENAI_API_KEY || process.env.MINIMAX_API_KEY),
  'configure a real model before using production research flows',
  'warn'
);

let failed = 0;
let warned = 0;
for (const check of checks) {
  const status = check.ok ? 'ok' : check.level === 'warn' ? 'warn' : 'fail';
  if (!check.ok && check.level === 'warn') warned += 1;
  if (!check.ok && check.level !== 'warn') failed += 1;
  console.log(`[${status}] ${check.name}: ${check.detail}`);
}

console.log(`\nSummary: ${checks.length - failed - warned} ok, ${warned} warnings, ${failed} failures`);
process.exit(failed > 0 ? 1 : 0);
