import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Col,
  Empty,
  Input,
  List,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Tooltip,
  Typography,
  message
} from 'antd';
import {
  ApiOutlined,
  BellOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DisconnectOutlined,
  LinkOutlined,
  ReloadOutlined,
  SendOutlined,
  ThunderboltOutlined,
  WarningOutlined
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { AppState } from '../types';
import {
  RealtimeMessageRecord,
  RealtimeMessageSeverity,
  StreamConnectionStatus,
  createRealtimeMessageStream,
  getRealtimePushUrl,
  getRealtimeStreamUrl,
  listRealtimeMessages,
  pushRealtimeMessage
} from '../services/realtimeMessageService';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

interface RealtimeMessagesProps {
  appState: AppState;
}

const severityMeta: Record<RealtimeMessageSeverity, { label: string; color: string }> = {
  info: { label: '信息', color: 'blue' },
  success: { label: '入库', color: 'green' },
  warning: { label: '预警', color: 'orange' },
  critical: { label: '紧急', color: 'red' }
};

const statusMeta: Record<StreamConnectionStatus, { text: string; badge: 'processing' | 'success' | 'warning' | 'default' | 'error' }> = {
  connecting: { text: '连接中', badge: 'processing' },
  live: { text: '监听中', badge: 'success' },
  reconnecting: { text: '重连中', badge: 'warning' },
  closed: { text: '已断开', badge: 'default' },
  error: { text: '异常', badge: 'error' }
};

const topicLabel: Record<string, string> = {
  'data-source-sync': '数据源同步',
  'data-source-upload': '本地上传',
  'data-source-crawl': '网页抓取',
  'data-source-keyword': '关键词抓取',
  'external-push': '外部推送',
  'data-source': '数据源'
};

const formatTime = (value: string) => dayjs(value).format('MM-DD HH:mm:ss');

const RealtimeMessages: React.FC<RealtimeMessagesProps> = ({ appState }) => {
  const defaultSymbol = appState.stocks[0]?.symbol || '';
  const streamRef = useRef<{ close: () => void } | null>(null);
  const [messages, setMessages] = useState<RealtimeMessageRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamConnectionStatus>('connecting');
  const [listenUrl, setListenUrl] = useState(getRealtimeStreamUrl());
  const [symbolFilter, setSymbolFilter] = useState<string | undefined>();
  const [severityFilter, setSeverityFilter] = useState<RealtimeMessageSeverity | undefined>();
  const [topicFilter, setTopicFilter] = useState<string | undefined>();
  const [pushing, setPushing] = useState(false);
  const [pushForm, setPushForm] = useState({
    title: '外部数据源推送',
    content: 'TSLA 舆情监控出现新的高热讨论，建议纳入今日复核清单。',
    symbol: defaultSymbol,
    severity: 'info' as RealtimeMessageSeverity,
    topic: 'external-push',
    sourceName: '外部推送接口',
    tags: '实时,推送'
  });

  const pushEndpoint = useMemo(() => getRealtimePushUrl(), []);

  const mergeIncomingMessage = useCallback((incoming: RealtimeMessageRecord) => {
    setMessages(prev => [
      incoming,
      ...prev.filter(item => item.id !== incoming.id)
    ].slice(0, 120));
  }, []);

  const loadMessages = useCallback(async () => {
    setLoading(true);
    try {
      const nextMessages = await listRealtimeMessages({ limit: 120 });
      setMessages(nextMessages);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '实时消息读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const connectStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = createRealtimeMessageStream({
      url: listenUrl,
      onMessage: mergeIncomingMessage,
      onStatus: setStreamStatus,
      onError: () => setStreamStatus(current => (current === 'closed' ? 'closed' : 'reconnecting'))
    });
  }, [listenUrl, mergeIncomingMessage]);

  useEffect(() => {
    void loadMessages();
  }, [loadMessages]);

  useEffect(() => {
    connectStream();
    return () => {
      streamRef.current?.close();
    };
  }, [connectStream]);

  const stockOptions = appState.stocks.map(stock => ({
    value: stock.symbol,
    label: `${stock.name} (${stock.symbol})`
  }));

  const topicOptions = useMemo(() => {
    const topics = Array.from(new Set(messages.map(item => item.topic).filter(Boolean)));
    return topics.map(topic => ({
      value: topic,
      label: topicLabel[topic] || topic
    }));
  }, [messages]);

  const visibleMessages = useMemo(() => {
    return messages.filter(item => {
      if (symbolFilter && item.symbol !== symbolFilter) {
        return false;
      }
      if (severityFilter && item.severity !== severityFilter) {
        return false;
      }
      if (topicFilter && item.topic !== topicFilter) {
        return false;
      }
      return true;
    });
  }, [messages, severityFilter, symbolFilter, topicFilter]);

  const latestMessage = messages[0];
  const criticalCount = messages.filter(item => item.severity === 'critical' || item.severity === 'warning').length;
  const dataSourceMessageCount = messages.filter(item => item.topic.startsWith('data-source')).length;

  const handlePush = async () => {
    if (!pushForm.title.trim()) {
      message.warning('请输入消息标题');
      return;
    }

    setPushing(true);
    try {
      const record = await pushRealtimeMessage({
        title: pushForm.title,
        content: pushForm.content,
        symbol: pushForm.symbol || undefined,
        severity: pushForm.severity,
        topic: pushForm.topic || 'external-push',
        source_name: pushForm.sourceName || '外部推送接口',
        source_type: 'server_api',
        tags: pushForm.tags.split(/[,，]/).map(tag => tag.trim()).filter(Boolean),
        metadata: {
          pushed_from: 'realtime-message-module',
          pushed_at: new Date().toISOString()
        }
      });
      mergeIncomingMessage(record);
      message.success('消息已推送');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '推送失败');
    } finally {
      setPushing(false);
    }
  };

  const copyPushEndpoint = async () => {
    try {
      await navigator.clipboard.writeText(pushEndpoint);
      message.success('推送入口已复制');
    } catch {
      message.info(pushEndpoint);
    }
  };

  return (
    <div className="realtime-message-shell">
      <div className="page-heading-band">
        <div>
          <Space align="center" size={10}>
            <span className="realtime-heading-icon"><ThunderboltOutlined /></span>
            <Title level={3} style={{ margin: 0 }}>实时消息</Title>
          </Space>
          <Text type="secondary">
            数据源同步、上传、抓取和外部推送会进入同一条消息流
          </Text>
        </div>
        <Space wrap>
          <Badge status={statusMeta[streamStatus].badge} text={statusMeta[streamStatus].text} />
          <Button icon={<ReloadOutlined />} onClick={() => { void loadMessages(); connectStream(); }}>
            刷新
          </Button>
          <Button icon={<DisconnectOutlined />} onClick={() => streamRef.current?.close()}>
            断开
          </Button>
        </Space>
      </div>

      <div className="realtime-kpi-grid">
        <div className="realtime-kpi-tile">
          <Statistic title="消息总数" value={messages.length} prefix={<BellOutlined />} />
        </div>
        <div className="realtime-kpi-tile">
          <Statistic title="数据源消息" value={dataSourceMessageCount} prefix={<ApiOutlined />} />
        </div>
        <div className="realtime-kpi-tile">
          <Statistic title="预警消息" value={criticalCount} prefix={<WarningOutlined />} />
        </div>
        <div className="realtime-kpi-tile">
          <Statistic title="最近时间" value={latestMessage ? formatTime(latestMessage.created_at) : '--'} prefix={<ClockCircleOutlined />} />
        </div>
      </div>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={7}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="realtime-control-panel">
              <div className="realtime-panel-title">
                <LinkOutlined />
                <span>监听流</span>
              </div>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Input
                  value={listenUrl}
                  onChange={event => setListenUrl(event.target.value)}
                  prefix="SSE"
                />
                <Button type="primary" block icon={<CheckCircleOutlined />} onClick={connectStream}>
                  连接监听
                </Button>
              </Space>
            </div>

            <div className="realtime-control-panel">
              <div className="realtime-panel-title">
                <SendOutlined />
                <span>推送入口</span>
              </div>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Input value={pushEndpoint} readOnly addonBefore="POST" />
                <Button block icon={<LinkOutlined />} onClick={copyPushEndpoint}>
                  复制入口
                </Button>
                <Input
                  value={pushForm.sourceName}
                  onChange={event => setPushForm(prev => ({ ...prev, sourceName: event.target.value }))}
                  placeholder="来源"
                />
                <Input
                  value={pushForm.title}
                  onChange={event => setPushForm(prev => ({ ...prev, title: event.target.value }))}
                  placeholder="标题"
                />
                <TextArea
                  rows={4}
                  value={pushForm.content}
                  onChange={event => setPushForm(prev => ({ ...prev, content: event.target.value }))}
                  placeholder="内容"
                />
                <Row gutter={8}>
                  <Col span={12}>
                    <Select
                      allowClear
                      value={pushForm.symbol || undefined}
                      onChange={value => setPushForm(prev => ({ ...prev, symbol: value || '' }))}
                      options={stockOptions}
                      placeholder="标的"
                      style={{ width: '100%' }}
                    />
                  </Col>
                  <Col span={12}>
                    <Select
                      value={pushForm.severity}
                      onChange={value => setPushForm(prev => ({ ...prev, severity: value }))}
                      options={Object.entries(severityMeta).map(([value, meta]) => ({ value, label: meta.label }))}
                      style={{ width: '100%' }}
                    />
                  </Col>
                </Row>
                <Input
                  value={pushForm.topic}
                  onChange={event => setPushForm(prev => ({ ...prev, topic: event.target.value }))}
                  placeholder="Topic"
                />
                <Input
                  value={pushForm.tags}
                  onChange={event => setPushForm(prev => ({ ...prev, tags: event.target.value }))}
                  placeholder="标签，逗号分隔"
                />
                <Button type="primary" block icon={<SendOutlined />} loading={pushing} onClick={handlePush}>
                  模拟推送
                </Button>
              </Space>
            </div>
          </Space>
        </Col>

        <Col xs={24} xl={17}>
          <div className="realtime-feed-panel">
            <div className="realtime-feed-toolbar">
              <div className="realtime-panel-title">
                <BellOutlined />
                <span>消息流</span>
              </div>
              <Space wrap>
                <Select
                  allowClear
                  value={symbolFilter}
                  onChange={setSymbolFilter}
                  options={stockOptions}
                  placeholder="标的"
                  style={{ width: 180 }}
                />
                <Select
                  allowClear
                  value={severityFilter}
                  onChange={setSeverityFilter}
                  options={Object.entries(severityMeta).map(([value, meta]) => ({ value, label: meta.label }))}
                  placeholder="级别"
                  style={{ width: 120 }}
                />
                <Select
                  allowClear
                  value={topicFilter}
                  onChange={setTopicFilter}
                  options={topicOptions}
                  placeholder="Topic"
                  style={{ width: 160 }}
                />
              </Space>
            </div>

            <List
              loading={loading}
              dataSource={visibleMessages}
              locale={{ emptyText: <Empty description="暂无实时消息" /> }}
              renderItem={item => (
                <List.Item className={`realtime-message-item severity-${item.severity}`}>
                  <div className="realtime-message-main">
                    <div className="realtime-message-head">
                      <Space wrap size={6}>
                        <Tag color={severityMeta[item.severity].color}>{severityMeta[item.severity].label}</Tag>
                        <Text strong>{item.title}</Text>
                        {item.symbol && <Tag>{item.symbol}</Tag>}
                        <Tag color="cyan">{topicLabel[item.topic] || item.topic}</Tag>
                      </Space>
                      <Tooltip title={dayjs(item.created_at).format('YYYY-MM-DD HH:mm:ss')}>
                        <Text type="secondary">{formatTime(item.created_at)}</Text>
                      </Tooltip>
                    </div>
                    {item.content && (
                      <Paragraph className="realtime-message-content" ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}>
                        {item.content}
                      </Paragraph>
                    )}
                    <div className="realtime-message-meta">
                      <Space wrap size={6}>
                        {item.source_name && <Text type="secondary">来源：{item.source_name}</Text>}
                        {item.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}
                        {item.url && (
                          <a href={item.url} target="_blank" rel="noreferrer">
                            原文
                          </a>
                        )}
                      </Space>
                    </div>
                  </div>
                </List.Item>
              )}
            />
          </div>
        </Col>
      </Row>
    </div>
  );
};

export default RealtimeMessages;
