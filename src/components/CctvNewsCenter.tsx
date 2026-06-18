import React, { useCallback, useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Empty,
  Input,
  Progress,
  Segmented,
  Select,
  Space,
  Statistic,
  Tag,
  Tooltip,
  Typography
} from 'antd';
import {
  BellOutlined,
  CalendarOutlined,
  ClockCircleOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { formatGeneratedAt } from '../utils/datetime';
import { AppState } from '../types';
import DataCenter from './DataCenter';
import './CctvNewsCenter.css';
import { pushRealtimeMessage } from '../services/eventService';
import {
  OfficialNewsItem,
  OfficialNewsResponse,
  OfficialNewsSource,
  getOfficialCctvNews
} from '../services/officialNewsService';

const { Paragraph, Text, Title } = Typography;
const { Search } = Input;

interface CctvNewsCenterProps {
  appState: AppState;
}

const sourceOptions: Array<{ label: string; value: OfficialNewsSource; hint: string }> = [
  { label: '新闻联播', value: 'xinwenlianbo', hint: '每日权威政策与宏观主线' },
  { label: '央视新闻', value: 'cctv-news', hint: '央视网新闻频道最新流' },
  { label: '央视财经', value: 'cctv-economy', hint: '经济、产业与外贸线索' }
];

const tagColor: Record<string, string> = {
  政策: 'red',
  宏观: 'blue',
  产业: 'green',
  风险: 'orange',
  国际: 'purple',
  财经: 'cyan',
  新闻联播: 'gold',
  完整节目: 'geekblue'
};

const formatDate = (value?: string | null) => {
  if (!value) return '--';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD') : value;
};

const normalizeText = (value: string) => value
  .toLowerCase()
  .replace(/[\s._\-—~～,，.。:：;；/\\()[\]【】《》"'“”‘’]+/g, '');

const matchesQuery = (item: OfficialNewsItem, query: string) => {
  const normalizedQuery = normalizeText(query);
  if (!normalizedQuery) return true;
  const haystack = normalizeText([
    item.title,
    item.summary,
    item.source_name,
    item.section,
    ...item.tags
  ].join(' '));
  return haystack.includes(normalizedQuery);
};

const buildPolicyNotes = (item: OfficialNewsItem): string[] => {
  const notes: string[] = [];
  if (item.tags.includes('政策')) {
    notes.push('政策信号：适合放入宏观假设、监管变量或主题催化复核。');
  }
  if (item.tags.includes('宏观')) {
    notes.push('宏观信号：关注消费、投资、外贸、财政或地产相关链条的二阶影响。');
  }
  if (item.tags.includes('产业')) {
    notes.push('产业信号：可映射到观察池里的产业链和龙头公司。');
  }
  if (item.tags.includes('风险')) {
    notes.push('风险信号：建议同步到持仓监控和事件日历。');
  }
  if (!notes.length) {
    notes.push('权威来源：可作为今日研究背景和证据库候选条目。');
  }
  return notes;
};

const CctvNewsCenter: React.FC<CctvNewsCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const [source, setSource] = useState<OfficialNewsSource>('xinwenlianbo');
  const [limit, setLimit] = useState(30);
  const [query, setQuery] = useState('');
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [pushingId, setPushingId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const data = await getOfficialCctvNews({ source, limit });
    setSelectedItemId(prev => (
      prev && data.items.some(item => item.id === prev)
        ? prev
        : data.items[0]?.id || null
    ));
    return data;
  }, [limit, source]);

  const sourceHint = useMemo(() => (
    sourceOptions.find(item => item.value === source)?.hint || '央视官方新闻源'
  ), [source]);

  const renderItemTags = (item: OfficialNewsItem) => (
    <Space wrap size={4}>
      {item.tags.slice(0, 6).map(tag => (
        <Tag key={tag} color={tagColor[tag] || 'default'}>{tag}</Tag>
      ))}
    </Space>
  );

  const pushToSignalFlow = async (item: OfficialNewsItem) => {
    setPushingId(item.id);
    try {
      await pushRealtimeMessage({
        title: `央视新闻：${item.title}`,
        content: item.summary || item.title,
        topic: 'official-news',
        severity: item.importance_score >= 78 ? 'warning' : 'info',
        source_name: item.source_name,
        source_type: 'web_page',
        url: item.url,
        tags: item.tags,
        metadata: {
          official_news_id: item.id,
          source: item.source,
          reported_date: item.reported_date,
          importance_score: item.importance_score,
          pushed_from: 'cctv-news-center'
        }
      });
      message.success('已推送到信号流');
    } catch (error: any) {
      message.error(error?.message || '推送失败');
    } finally {
      setPushingId(null);
    }
  };

  return (
    <DataCenter<OfficialNewsResponse>
      className="cctv-news-page"
      title="央视新闻专区"
      subtitle={`官方源：${sourceHint}。用于捕捉政策、宏观、产业和风险信号。`}
      fetchData={fetchData}
      refreshLabel="刷新官方源"
      emptyText="等待央视官方源返回新闻"
      extra={(
        <Space className="cctv-news-header-actions" wrap>
          <Segmented
            value={source}
            onChange={value => setSource(value as OfficialNewsSource)}
            options={sourceOptions.map(item => ({ label: item.label, value: item.value }))}
          />
          <Select
            value={limit}
            onChange={setLimit}
            options={[20, 30, 50, 80].map(value => ({ value, label: `${value} 条` }))}
            style={{ width: 104 }}
          />
        </Space>
      )}
      renderContent={(data, refresh) => {
        const visibleItems = data.items.filter(item => matchesQuery(item, query));
        const selectedItem = visibleItems.find(item => item.id === selectedItemId)
          || visibleItems[0]
          || data.items[0];
        const policyCount = data.items.filter(item => item.tags.includes('政策')).length;
        const macroCount = data.items.filter(item => item.tags.includes('宏观')).length;
        const riskCount = data.items.filter(item => item.tags.includes('风险')).length;
        const latestDate = data.items[0]?.reported_date || data.items[0]?.published_at;

        return (
          <>
            {data.warnings.length ? (
              <Alert
                className="cctv-news-warning"
                type="warning"
                showIcon
                message={data.warnings.slice(0, 3).join('；')}
              />
            ) : null}

            <div className="cctv-news-kpi-grid">
              <div className="cctv-news-kpi">
                <Statistic title="解析条目" value={data.total_found} suffix="条" prefix={<BellOutlined />} />
                <Text type="secondary">当前展示 {visibleItems.length}</Text>
              </div>
              <div className="cctv-news-kpi">
                <Statistic title="政策/宏观" value={policyCount + macroCount} suffix="条" prefix={<ThunderboltOutlined />} />
                <Text type="secondary">政策 {policyCount} · 宏观 {macroCount}</Text>
              </div>
              <div className="cctv-news-kpi">
                <Statistic title="风险信号" value={riskCount} suffix="条" prefix={<SafetyCertificateOutlined />} />
                <Text type="secondary">可同步持仓监控</Text>
              </div>
              <div className="cctv-news-kpi">
                <Statistic title="最新日期" value={formatDate(latestDate)} prefix={<CalendarOutlined />} />
                <Text type="secondary">缓存 {data.cache_age_seconds}s</Text>
              </div>
            </div>

            <div className="cctv-news-toolbar">
              <Search
                allowClear
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder="搜政策、行业、风险或关键词"
                style={{ maxWidth: 360 }}
              />
              <Space wrap size={8}>
                <Tag color="blue">生成 {formatGeneratedAt(data.generated_at)}</Tag>
                <a href={data.source_url} target="_blank" rel="noreferrer">
                  官方页面 <LinkOutlined />
                </a>
                <Button icon={<ReloadOutlined />} onClick={refresh}>
                  重新拉取
                </Button>
              </Space>
            </div>

            <div className="cctv-news-layout">
              <div className="cctv-news-list-panel">
                {visibleItems.length ? (
                  <div className="cctv-news-list">
                    {visibleItems.map(item => (
                      <button
                        key={item.id}
                        type="button"
                        className={`cctv-news-row ${item.id === selectedItem?.id ? 'active' : ''}`}
                        onClick={() => setSelectedItemId(item.id)}
                      >
                        <div className="cctv-news-row-main">
                          <Text strong>{item.title}</Text>
                          {item.summary && (
                            <Paragraph ellipsis={{ rows: 2 }} className="cctv-news-row-summary">
                              {item.summary}
                            </Paragraph>
                          )}
                          <div className="cctv-news-row-tags">
                            {renderItemTags(item)}
                          </div>
                        </div>
                        <div className="cctv-news-row-meta">
                          <Text type="secondary">{formatDate(item.reported_date || item.published_at)}</Text>
                          <Tooltip title="权威信号评分">
                            <Progress
                              type="circle"
                              size={46}
                              percent={item.importance_score}
                              format={value => `${value}`}
                            />
                          </Tooltip>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <Empty description="没有匹配的央视新闻" />
                )}
              </div>

              <div className="cctv-news-detail-panel">
                {selectedItem ? (
                  <>
                    <div className="cctv-news-detail-head">
                      <div>
                        <Text type="secondary">{selectedItem.source_name} · {selectedItem.section}</Text>
                        <Title level={4}>{selectedItem.title}</Title>
                      </div>
                      <Tag color={selectedItem.importance_score >= 78 ? 'orange' : 'blue'}>
                        {selectedItem.importance_score} 分
                      </Tag>
                    </div>

                    <div className="cctv-news-detail-tags">
                      {renderItemTags(selectedItem)}
                    </div>

                    <div className="cctv-news-detail-summary">
                      <ClockCircleOutlined />
                      <span>{formatDate(selectedItem.reported_date || selectedItem.published_at)}</span>
                      <span>{selectedItem.url.replace(/^https?:\/\//, '').slice(0, 72)}</span>
                    </div>

                    {selectedItem.summary && (
                      <Paragraph className="cctv-news-detail-text">
                        {selectedItem.summary}
                      </Paragraph>
                    )}

                    <div className="cctv-news-notes">
                      {buildPolicyNotes(selectedItem).map(note => (
                        <div key={note}>{note}</div>
                      ))}
                      <div>当前观察池标的：{appState.stocks.length} 个，可结合个股研究继续下钻。</div>
                    </div>

                    <div className="cctv-news-detail-actions">
                      <Button
                        type="primary"
                        icon={<SendOutlined />}
                        loading={pushingId === selectedItem.id}
                        onClick={() => pushToSignalFlow(selectedItem)}
                      >
                        推送到信号流
                      </Button>
                      <Button href={selectedItem.url} target="_blank" icon={<LinkOutlined />}>
                        打开原文
                      </Button>
                    </div>
                  </>
                ) : (
                  <Empty description="请选择一条央视新闻" />
                )}
              </div>
            </div>
          </>
        );
      }}
    />
  );
};

export default CctvNewsCenter;
