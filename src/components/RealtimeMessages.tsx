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
  Switch,
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
import CenterShell from './common/CenterShell';
import './RealtimeMessages.css';
import {
  RealtimeMessageRecord,
  RealtimeMessageSeverity,
  StreamConnectionStatus,
  createRealtimeMessageStream,
  getRealtimePushUrl,
  getRealtimeStreamUrl,
  listRealtimeMessages,
  pushRealtimeMessage,
  createRecallSubscription,
  listRecallSubscriptions,
  deleteRecallSubscription,
  getRecallMetrics,
  RecallSubscriptionRecord,
  RecallMetrics
} from '../services/eventService';
import { useRecallPrefs, fireTestRecall, RecallScope } from '../utils/signalRecall';

const { Text, Paragraph } = Typography;
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
  'official-news': '权威新闻',
  'people-voice': '人物跟踪',
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
  const recall = useRecallPrefs();
  const [recallEmail, setRecallEmail] = useState('');
  const [subscribing, setSubscribing] = useState(false);
  const [emailSubscriptions, setEmailSubscriptions] = useState<RecallSubscriptionRecord[]>([]);
  const [recallMetrics, setRecallMetrics] = useState<RecallMetrics | null>(null);

  const loadEmailSubscriptions = useCallback(async () => {
    try {
      const subs = await listRecallSubscriptions();
      setEmailSubscriptions(subs.filter(sub => sub.channel === 'email'));
    } catch {
      // 后端未就绪时静默：浏览器通知召回不依赖它
    }
  }, []);

  const loadRecallMetrics = useCallback(async () => {
    try {
      setRecallMetrics(await getRecallMetrics());
    } catch {
      // 后端未就绪时静默
    }
  }, []);

  useEffect(() => {
    void loadEmailSubscriptions();
    void loadRecallMetrics();
  }, [loadEmailSubscriptions, loadRecallMetrics]);

  const handleSubscribeEmail = useCallback(async () => {
    const email = recallEmail.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      message.warning('请输入有效邮箱');
      return;
    }
    setSubscribing(true);
    try {
      const watchlist = appState.stocks.map(stock => stock.symbol).filter(Boolean);
      await createRecallSubscription({
        channel: 'email',
        address: email,
        symbols: recall.prefs.scope === 'watchlist' ? watchlist : [],
        severities: recall.prefs.severities,
        scope: recall.prefs.scope
      });
      setRecallEmail('');
      message.success('已订阅邮件召回（实际发信需后端配置 SMTP）');
      await loadEmailSubscriptions();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '订阅失败');
    } finally {
      setSubscribing(false);
    }
  }, [recallEmail, appState.stocks, recall.prefs.scope, recall.prefs.severities, loadEmailSubscriptions]);

  const handleUnsubscribeEmail = useCallback(async (id: string) => {
    try {
      await deleteRecallSubscription(id);
      await loadEmailSubscriptions();
    } catch {
      message.error('取消订阅失败');
    }
  }, [loadEmailSubscriptions]);

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
    <CenterShell
      icon={<ThunderboltOutlined />}
      title="实时消息"
      subtitle="数据源同步、上传、抓取和外部推送会进入同一条消息流"
      actions={(
        <Space wrap>
          <Badge status={statusMeta[streamStatus].badge} text={statusMeta[streamStatus].text} />
          <Button icon={<ReloadOutlined />} onClick={() => { void loadMessages(); connectStream(); }}>
            刷新
          </Button>
          <Button icon={<DisconnectOutlined />} onClick={() => streamRef.current?.close()}>
            断开
          </Button>
        </Space>
      )}
    >
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

      <Row className="realtime-layout" gutter={[14, 14]}>
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
                <BellOutlined />
                <span>信号召回</span>
              </div>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <div className="realtime-recall-row">
                  <span>浏览器通知</span>
                  <Switch
                    checked={recall.prefs.browserEnabled}
                    onChange={on => (on ? void recall.enableBrowser() : recall.disableBrowser())}
                  />
                </div>
                {recall.permission === 'denied' && (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    浏览器已拒绝通知权限，请在站点设置里手动开启。
                  </Text>
                )}
                {recall.permission === 'unsupported' && (
                  <Text type="secondary" style={{ fontSize: 12 }}>当前环境不支持桌面通知。</Text>
                )}
                <div className="realtime-recall-row">
                  <span>触发级别</span>
                  <Select
                    mode="multiple"
                    value={recall.prefs.severities}
                    onChange={value => recall.setPrefs({ severities: value as RealtimeMessageSeverity[] })}
                    options={Object.entries(severityMeta).map(([value, meta]) => ({ value, label: meta.label }))}
                    placeholder="级别"
                    style={{ flex: 1, minWidth: 140 }}
                  />
                </div>
                <div className="realtime-recall-row">
                  <span>范围</span>
                  <Select
                    value={recall.prefs.scope}
                    onChange={value => recall.setPrefs({ scope: value as RecallScope })}
                    options={[
                      { value: 'watchlist', label: '仅自选股' },
                      { value: 'all', label: '全部信号' }
                    ]}
                    style={{ minWidth: 140 }}
                  />
                </div>
                <div className="realtime-recall-row">
                  <span>仅离开时提醒</span>
                  <Switch
                    checked={recall.prefs.onlyWhenHidden}
                    onChange={on => recall.setPrefs({ onlyWhenHidden: on })}
                  />
                </div>
                <Button
                  block
                  icon={<BellOutlined />}
                  onClick={() => {
                    if (!fireTestRecall()) {
                      message.info('请先开启浏览器通知并授予权限');
                    }
                  }}
                >
                  测试召回
                </Button>
                <div className="realtime-recall-row">
                  <span>邮件召回</span>
                  <Space.Compact style={{ flex: 1 }}>
                    <Input
                      value={recallEmail}
                      onChange={event => setRecallEmail(event.target.value)}
                      onPressEnter={() => void handleSubscribeEmail()}
                      placeholder="you@example.com"
                      type="email"
                    />
                    <Button type="primary" loading={subscribing} onClick={() => void handleSubscribeEmail()}>
                      订阅
                    </Button>
                  </Space.Compact>
                </div>
                {emailSubscriptions.length > 0 && (
                  <Space wrap size={6}>
                    {emailSubscriptions.map(sub => (
                      <Tag
                        key={sub.id}
                        closable
                        onClose={event => {
                          event.preventDefault();
                          void handleUnsubscribeEmail(sub.id);
                        }}
                      >
                        {sub.address}
                      </Tag>
                    ))}
                  </Space>
                )}
                {recallMetrics && (recallMetrics.delivered > 0 || recallMetrics.skipped > 0 || recallMetrics.error > 0) && (
                  <div className="realtime-recall-row" style={{ fontSize: 12 }}>
                    <span>回流度量</span>
                    <Text type="secondary" style={{ flex: 1, textAlign: 'right' }}>
                      送达 {recallMetrics.delivered} · 点击 {recallMetrics.clicked} · 回流率{' '}
                      {(recallMetrics.click_through_rate * 100).toFixed(0)}%
                      {recallMetrics.skipped > 0 ? ` · 跳过 ${recallMetrics.skipped}` : ''}
                    </Text>
                  </div>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  浏览器通知=在线召回；邮件=离线召回（实际发信需后端配置 SMTP，未配置则记为已跳过）。点击邮件/推送链接可被记为回流。
                </Text>
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
    </CenterShell>
  );
};

export default RealtimeMessages;
