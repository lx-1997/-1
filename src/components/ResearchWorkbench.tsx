import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  ExportOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  WarningOutlined
} from '@ant-design/icons';

const { Text, Title } = Typography;

interface WorkbenchJob {
  id: string;
  status: string;
}

interface WorkbenchStatus {
  credentialsAvailable: boolean;
  jobs: WorkbenchJob[];
}

const DEFAULT_WORKBENCH_URL = 'http://127.0.0.1:3927';
const START_COMMAND = 'npm run research-workbench';
const INSTALL_COMMAND = 'npm run research-workbench:install';

const jobStatusLabel: Record<string, string> = {
  running: '运行中',
  stopping: '停止中',
  stopped: '已停止',
  completed: '完成',
  failed: '失败'
};

const normalizeBaseUrl = (value: string) => value.replace(/\/+$/, '');

const ResearchWorkbench: React.FC = () => {
  const workbenchUrl = useMemo(
    () => normalizeBaseUrl(process.env.REACT_APP_RESEARCH_WORKBENCH_URL || DEFAULT_WORKBENCH_URL),
    []
  );
  const [status, setStatus] = useState<WorkbenchStatus | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [checking, setChecking] = useState(true);

  const checkStatus = useCallback(async () => {
    setChecking(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 2500);

    try {
      const response = await fetch(`${workbenchUrl}/api/status`, {
        cache: 'no-store',
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setStatus(await response.json());
      setOnline(true);
    } catch (error) {
      setStatus(null);
      setOnline(false);
    } finally {
      window.clearTimeout(timer);
      setChecking(false);
    }
  }, [workbenchUrl]);

  useEffect(() => {
    void checkStatus();
    const timer = window.setInterval(() => {
      void checkStatus();
    }, 30000);

    return () => window.clearInterval(timer);
  }, [checkStatus]);

  const latestJob = status?.jobs?.[0];

  return (
    <div className="research-workbench-page">
      <div className="research-workbench-toolbar">
        <div className="research-workbench-title">
          <span className="research-workbench-mark">
            <FileSearchOutlined />
          </span>
          <div>
            <Title level={4}>研报工作台</Title>
            <Text type="secondary">{workbenchUrl}</Text>
          </div>
        </div>

        <Space wrap size="small" className="research-workbench-actions">
          <Tag color={online ? 'green' : frameLoaded ? 'blue' : online === false ? 'red' : 'default'}>
            {online ? '已连接' : frameLoaded ? '已打开' : online === false ? '未启动' : '检测中'}
          </Tag>
          {online && (
            <Tag color={status?.credentialsAvailable ? 'green' : 'orange'}>
              {status?.credentialsAvailable ? '凭证已配置' : '凭证未配置'}
            </Tag>
          )}
          {latestJob && (
            <Tag color={latestJob.status === 'failed' ? 'red' : latestJob.status === 'running' ? 'blue' : 'default'}>
              {jobStatusLabel[latestJob.status] || latestJob.status}
            </Tag>
          )}
          <Tooltip title="刷新状态">
            <Button
              icon={<ReloadOutlined spin={checking} />}
              onClick={() => void checkStatus()}
            />
          </Tooltip>
          <Tooltip title="打开新窗口">
            <Button
              icon={<ExportOutlined />}
              onClick={() => window.open(workbenchUrl, '_blank', 'noopener,noreferrer')}
            />
          </Tooltip>
        </Space>
      </div>

      {online === false && !frameLoaded && (
        <Alert
          className="research-workbench-alert"
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message="研报工作台服务未启动"
          description={
            <Space direction="vertical" size={6}>
              <Text>
                首次运行先执行 <Text code copyable>{INSTALL_COMMAND}</Text>
              </Text>
              <Text>
                然后启动 <Text code copyable>{START_COMMAND}</Text>
              </Text>
            </Space>
          }
        />
      )}

      <div className="research-workbench-frame-shell">
        <iframe
          className="research-workbench-frame"
          title="研报工作台"
          src={workbenchUrl}
          allow="clipboard-read; clipboard-write"
          onLoad={() => {
            setFrameLoaded(true);
          }}
        />
        {online === null && !frameLoaded ? (
          <div className="research-workbench-empty">
            <Spin />
          </div>
        ) : online === false && !frameLoaded ? (
          <div className="research-workbench-empty">
            <Space direction="vertical" align="center" size={12}>
              <LinkOutlined />
              <Text type="secondary">等待本地模块服务</Text>
            </Space>
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default ResearchWorkbench;
