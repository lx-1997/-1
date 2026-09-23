#!/usr/bin/env node
/** Run the Capacitor Android Gradle wrapper on every host shell. */
const path = require('path');
const { spawnSync } = require('child_process');

const task = process.argv[2] || 'assembleDebug';
const gradleBin = process.platform === 'win32' ? 'gradlew.bat' : './gradlew';
const result = spawnSync(gradleBin, [task], {
  cwd: path.join(__dirname, '..', 'android'),
  stdio: 'inherit',
  shell: process.platform === 'win32'
});

if (result.error) {
  console.error(`[android-build] ${result.error.message}`);
}
process.exit(result.status || (result.error ? 1 : 0));
