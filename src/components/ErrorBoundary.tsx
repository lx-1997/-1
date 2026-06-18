import React from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

const CHUNK_RELOAD_FLAG = 'deepfocus:chunk-reload-attempted';

const isChunkLoadError = (error: Error | null): boolean => {
  if (!error) {
    return false;
  }
  const message = `${error.name || ''} ${error.message || ''}`;
  return /ChunkLoadError|Loading chunk .* failed|dynamically imported module/i.test(message);
};

// 浏览器/微信自动翻译篡改 DOM 后，React 更新会抛 removeChild/insertBefore 错误——可静默重渲染恢复
const isDomMutationError = (error: Error | null): boolean => {
  if (!error) return false;
  const message = `${error.name || ''} ${error.message || ''}`;
  return /removeChild|insertBefore|appendChild|not a child of this node|The node (to be removed|before which)/i.test(message);
};

class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, error: null };
  private domRecoverCount = 0;

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.props.onError?.(error, errorInfo);
    if (isChunkLoadError(error) && !window.sessionStorage.getItem(CHUNK_RELOAD_FLAG)) {
      window.sessionStorage.setItem(CHUNK_RELOAD_FLAG, String(Date.now()));
      window.setTimeout(() => window.location.reload(), 100);
      return;
    }
    // 翻译插件等导致的 DOM 错位：静默重渲染恢复，不打断用户（含实时推送）
    if (isDomMutationError(error) && this.domRecoverCount < 5) {
      this.domRecoverCount += 1;
      window.setTimeout(() => this.setState({ hasError: false, error: null }), 60);
    }
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  handleRefresh = () => {
    window.sessionStorage.removeItem(CHUNK_RELOAD_FLAG);
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      const chunkLoadError = isChunkLoadError(this.state.error);
      return (
        <div style={{ padding: '60px 24px', textAlign: 'center' }}>
          <Result
            status="error"
            title={chunkLoadError ? '资源包加载失败' : '页面异常'}
            subTitle={
              chunkLoadError
                ? '前端资源已更新或服务刚重启，请刷新页面重新拉取最新模块。'
                : this.state.error?.message || '组件渲染出错，请刷新重试'
            }
            extra={chunkLoadError ? [
                <Button key="refresh" type="primary" onClick={this.handleRefresh}>
                  刷新页面
                </Button>
              ] : [
                <Button key="retry" type="primary" onClick={this.handleReset}>
                  重试
                </Button>,
              <Button key="reload" onClick={this.handleRefresh}>
                刷新页面
              </Button>
            ]}
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
