import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Empty, Spin, App as AntdApp } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import CenterShell from './common/CenterShell';
import type { DataQuality } from '../types';

export interface DataCenterProps<T> {
  title: string;
  subtitle?: string;
  fetchData: () => Promise<T>;
  renderContent: (data: T, refresh: () => void) => React.ReactNode;
  emptyText?: string;
  className?: string;
  refreshLabel?: string;
  extra?: React.ReactNode;
}

function DataCenter<T>({
  title,
  subtitle,
  fetchData,
  renderContent,
  emptyText = '暂无数据',
  className,
  refreshLabel = '刷新',
  extra
}: DataCenterProps<T>) {
  const { message } = AntdApp.useApp();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchData();
      setData(result);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '数据加载失败';
      setError(detail);
      message.error(detail);
    } finally {
      setLoading(false);
    }
  }, [fetchData, message]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  return (
    <CenterShell
      // 保留 data-center-page 作为后代样式钩子（index.css 用 `.data-center-page .ant-table/.ant-card/...`
      // 给中心内的表格/卡片/统计做暗色适配）；CenterShell 提供等价的布局/留白，二者同值不冲突。
      className={['data-center-page', className].filter(Boolean).join(' ')}
      title={title}
      subtitle={subtitle}
      dataQuality={(data as { data_quality?: DataQuality } | null)?.data_quality ?? null}
      actions={(
        <>
          {extra}
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={loadData}
          >
            {refreshLabel}
          </Button>
        </>
      )}
    >
      {error && (
        <Alert
          className="data-center-alert"
          type="error"
          showIcon
          message="数据加载失败"
          description={error}
          action={
            <Button size="small" onClick={loadData}>
              重试
            </Button>
          }
        />
      )}

      <Spin spinning={loading}>
        {data
          ? renderContent(data, loadData)
          : !loading && !error && (
            <Empty description={emptyText} />
          )}
      </Spin>
    </CenterShell>
  );
}

export default DataCenter;
