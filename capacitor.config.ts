import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.deepfocus.trading',
  appName: '深度焦点',
  webDir: 'build',
  server: {
    // 远程加载生产站点：前端每次在服务器上重新构建，App 即自动获得最新版本
    url: 'https://daocaijing.com',
    androidScheme: 'https',
    allowNavigation: ['daocaijing.com', 'www.daocaijing.com']
  },
  android: {
    allowMixedContent: false
  }
};

export default config;
