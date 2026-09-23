import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.daocaijing.app',
  appName: 'Daocaijing',
  webDir: 'build',
  android: {
    allowMixedContent: false
  },
  // Android WebView 的页面来源是 https://localhost，而生产 API 不返回
  // Capacitor 所需的 CORS 头。启用 CapacitorHttp 后，fetch/XHR（包括
  // axios）改走 Android 原生网络，不再受 WebView 跨域策略影响。
  plugins: {
    CapacitorHttp: {
      enabled: true
    }
  }
};

// 默认把前端资源打进 APK，避免应用退化成只能打开网页的壳。需要联调
// 线上站点时，可显式设置 CAPACITOR_SERVER_URL 后再执行 cap sync。
const remoteUrl = process.env.CAPACITOR_SERVER_URL?.trim();
if (remoteUrl) {
  let remote: URL;
  try {
    remote = new URL(remoteUrl);
  } catch {
    throw new Error(`CAPACITOR_SERVER_URL must be a valid HTTPS URL: ${remoteUrl}`);
  }
  if (remote.protocol !== 'https:' || !['daocaijing.com', 'www.daocaijing.com'].includes(remote.hostname.toLowerCase())) {
    throw new Error(`CAPACITOR_SERVER_URL must use daocaijing.com over HTTPS: ${remoteUrl}`);
  }
  config.server = {
    url: remoteUrl,
    androidScheme: 'https',
    allowNavigation: ['daocaijing.com', 'www.daocaijing.com']
  };
}

export default config;
