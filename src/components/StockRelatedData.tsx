import React, { useEffect, useMemo, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Empty,
  Input,
  List,
  Progress,
  Select,
  Space,
  Spin,
  Tag,
  Typography
} from 'antd';
import {
  DatabaseOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons';
import { DataSourceCategory, DataSourceItemRecord, DataSourceType, listDataItems } from '../services/dataSourceService';
import { Stock, ViewType } from '../types';

const { Paragraph, Text } = Typography;
const { Search } = Input;

interface StockRelatedDataProps {
  stock: Stock;
  onViewChange?: (view: ViewType) => void;
}

const sourceTypeOptions: Array<{ value: DataSourceType | 'all'; label: string }> = [
  { value: 'all', label: '全部来源' },
  { value: 'server_api', label: '服务器接口' },
  { value: 'market_api', label: '行情 API' },
  { value: 'upload', label: '本地上传' },
  { value: 'web_page', label: '网页源' },
  { value: 'agent_crawl', label: 'Agent 抓取' },
  { value: 'manual', label: '手工资料' }
];

const sourceTypeMeta: Record<DataSourceType, { label: string; color: string }> = {
  server_api: { label: '服务器接口', color: 'blue' },
  market_api: { label: '行情 API', color: 'purple' },
  upload: { label: '本地上传', color: 'green' },
  web_page: { label: '网页源', color: 'cyan' },
  agent_crawl: { label: 'Agent 抓取', color: 'orange' },
  manual: { label: '手工资料', color: 'default' }
};

const sourceCategoryMeta: Record<DataSourceCategory, { label: string; color: string }> = {
  market: { label: '行情数据', color: 'purple' },
  earnings: { label: '财报日历', color: 'blue' },
  filing: { label: '公告披露', color: 'geekblue' },
  research: { label: '研报资料', color: 'cyan' },
  sentiment: { label: '舆情社区', color: 'orange' },
  upload: { label: '用户上传', color: 'green' },
  internal: { label: '内部数据', color: 'gold' },
  other: { label: '其他', color: 'default' }
};

const formatDate = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
};

const StockRelatedData: React.FC<StockRelatedDataProps> = ({ stock, onViewChange }) => {
  const { message } = AntdApp.useApp();
  const [items, setItems] = useState<DataSourceItemRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [sourceType, setSourceType] = useState<DataSourceType | 'all'>('all');

  const loadItems = async () => {
    setLoading(true);
    try {
      const nextItems = await listDataItems({
        symbol: stock.symbol,
        query: query.trim() || undefined,
        source_type: sourceType === 'all' ? undefined : sourceType,
        sort: 'time_desc',
        limit: 80
      });
      setItems(nextItems);
    } catch (error: any) {
      setItems([]);
      message.error(error?.response?.data?.detail || '关联数据加载失败，请确认后端数据源服务已启动');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setQuery('');
    setSourceType('all');
  }, [stock.symbol]);

  useEffect(() => {
    loadItems();
  }, [stock.symbol, sourceType]);

  const stats = useMemo(() => {
    const sourceCount = new Set(items.map(item => item.source_id)).size;
    const avgCredibility = items.length
      ? Math.round(items.reduce((sum, item) => sum + item.credibility_score, 0) / items.length * 100)
      : 0;
    const tags = Array.from(new Set(items.flatMap(item => item.tags))).slice(0, 8);
    return { sourceCount, avgCredibility, tags };
  }, [items]);

  return (
    <div className="stock-data-panel">
      <div className="stock-data-header">
        <div>
          <div className="dashboard-eyebrow">LINKED DATA</div>
          <h3 className="stock-data-title">{stock.name} 关联资料库</h3>
          <Text type="secondary">
            按标的代码 {stock.symbol} 从数据源中心读取资料，可用于 RAG 检索、多 Agent 证据审查和投研报告生成。
          </Text>
        </div>
        <Space wrap>
          <Button icon={<DatabaseOutlined />} onClick={() => onViewChange?.('data-sources')}>
            数据源中心
          </Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadItems}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="stock-data-stats">
        <div className="stock-data-stat">
          <span>资料条目</span>
          <strong>{items.length}</strong>
        </div>
        <div className="stock-data-stat">
          <span>来源数量</span>
          <strong>{stats.sourceCount}</strong>
        </div>
        <div className="stock-data-stat">
          <span>平均可信度</span>
          <strong>{stats.avgCredibility}%</strong>
        </div>
      </div>

      <div className="stock-data-toolbar">
        <Search
          allowClear
          value={query}
          placeholder={`搜索 ${stock.symbol} 关联资料`}
          enterButton={<FileSearchOutlined />}
          onChange={event => setQuery(event.target.value)}
          onSearch={loadItems}
          style={{ maxWidth: 420 }}
        />
        <Select
          value={sourceType}
          onChange={setSourceType}
          options={sourceTypeOptions}
          style={{ width: 150 }}
        />
      </div>

      {stats.tags.length > 0 && (
        <div className="stock-data-tags">
          <Text type="secondary">常见标签：</Text>
          {stats.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}
        </div>
      )}

      <Spin spinning={loading}>
        {items.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={`${stock.symbol} 暂无关联资料`}
          >
            <Space wrap>
              <Button type="primary" icon={<DatabaseOutlined />} onClick={() => onViewChange?.('data-sources')}>
                去数据源中心入库
              </Button>
              <Button icon={<ReloadOutlined />} loading={loading} onClick={loadItems}>
                重新加载
              </Button>
            </Space>
          </Empty>
        ) : (
          <List
            itemLayout="vertical"
            dataSource={items}
            renderItem={item => (
              <List.Item className="stock-data-item">
                <div className="stock-data-item-head">
                  <Space size={8} wrap>
                    <Text strong>{item.title}</Text>
                    <Tag color={sourceCategoryMeta[item.source_category || 'other'].color}>
                      {sourceCategoryMeta[item.source_category || 'other'].label}
                    </Tag>
                    <Tag color={sourceTypeMeta[item.source_type].color}>
                      {sourceTypeMeta[item.source_type].label}
                    </Tag>
                    <Tag color="blue">{item.source_name}</Tag>
                  </Space>
                  <Text type="secondary">{formatDate(item.collected_at)}</Text>
                </div>
                <Paragraph ellipsis={{ rows: 2 }} className="stock-data-preview">
                  {item.text_preview || item.text}
                </Paragraph>
                <div className="stock-data-meta">
                  <Space wrap>
                    <span>
                      <SafetyCertificateOutlined /> 可信度 {Math.round(item.credibility_score * 100)}%
                    </span>
                    <Progress
                      percent={Math.round(item.credibility_score * 100)}
                      size="small"
                      showInfo={false}
                      style={{ width: 90 }}
                    />
                    {item.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}
                    {item.url && (
                      <Button
                        type="link"
                        size="small"
                        icon={<LinkOutlined />}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        原文
                      </Button>
                    )}
                  </Space>
                </div>
              </List.Item>
            )}
          />
        )}
      </Spin>
    </div>
  );
};

export default StockRelatedData;
