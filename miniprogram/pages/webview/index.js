const HOST = 'https://daocaijing.com';
const DEFAULT_PATH = '/';

function sanitizePath(raw) {
  if (!raw || typeof raw !== 'string') return DEFAULT_PATH;
  let p = raw.trim();
  if (!p) return DEFAULT_PATH;
  if (!p.startsWith('/')) p = '/' + p;
  if (p.indexOf('//') === 0 || p.indexOf('..') !== -1 || p.indexOf('\\') !== -1) {
    return DEFAULT_PATH;
  }
  return p;
}

Page({
  data: {
    src: HOST + DEFAULT_PATH,
    loadError: false,
  },

  onLoad(query) {
    const path = sanitizePath(query && query.path);
    this.setData({ src: HOST + path });
  },

  onLoaded() {
    if (this.data.loadError) {
      this.setData({ loadError: false });
    }
  },

  onError() {
    this.setData({ loadError: true });
  },

  onRetry() {
    const src = this.data.src;
    this.setData({ src: '', loadError: false }, () => {
      this.setData({ src });
    });
  },

  onShareAppMessage() {
    return {
      title: '稻草财经 · 实时快讯 · 投行研报 · AI 解读',
      path: '/pages/webview/index',
      imageUrl: HOST + '/og-cover.png',
    };
  },

  onShareTimeline() {
    return {
      title: '稻草财经 · 实时快讯 · 投行研报 · AI 解读',
      query: '',
    };
  },
});
