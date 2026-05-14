import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
  message
} from 'antd';
import {
  ApiOutlined,
  BulbOutlined,
  ExperimentOutlined,
  LineChartOutlined,
  ReloadOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { AiResearchReport, analyzeStock, checkAiApiHealth } from '../services/aiResearchService';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface AiResearchProps {
  appState: AppState;
}

const sentimentMap = {
  positive: { text: '偏积极', color: 'green' },
  neutral: { text: '中性', color: 'blue' },
  negative: { text: '偏谨慎', color: 'red' }
};

const riskMap = {
  low: { text: '低', color: '#52c41a' },
  medium: { text: '中', color: '#faad14' },
  high: { text: '高', color: '#ff4d4f' }
};

const AiResearch: React.FC<AiResearchProps> = ({ appState }) => {
  const [selectedSymbol, setSelectedSymbol] = useState(appState.stocks[0]?.symbol);
  const [question, setQuestion] = useState('');
  const [report, setReport] = useState<AiResearchReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<{ status: string; provider: string; model: string } | null>(null);

  const selectedStock = useMemo(
    () => appState.stocks.find(stock => stock.symbol === selectedSymbol) || appState.stocks[0],
    [appState.stocks, selectedSymbol]
  );

  const relatedPosts = useMemo(
    () => selectedStock
      ? appState.posts.filter(post => post.stockSymbol === selectedStock.symbol).slice(0, 8)
      : [],
    [appState.posts, selectedStock]
  );

  useEffect(() => {
    checkAiApiHealth()
      .then(setApiStatus)
      .catch(() => setApiStatus(null));
  }, []);

  const handleAnalyze = async () => {
    if (!selectedStock) {
      message.warning('暂无可分析的股票');
      return;
    }

    setLoading(true);
    try {
      const data = await analyzeStock({
        stock: selectedStock,
        posts: relatedPosts.map(post => ({
          title: post.title,
          summary: post.summary,
          content: post.content,
          category: post.category,
          tags: post.tags,
          qualityScore: post.qualityScore,
          publishTime: post.publishTime
        })),
        question,
        locale: 'zh-CN'
      });
      setReport(data);
      message.success('AI投研报告已生成');
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动并配置模型';
      message.error(`生成失败：${detail}`);
    } finally {
      setLoading(false);
    }
  };

  if (!selectedStock) {
    return (
      <div style={{ padding: 16 }}>
        <Empty description="暂无股票数据" />
      </div>
    );
  }

  const sentiment = report ? sentimentMap[report.sentiment_label] : null;
  const risk = report ? riskMap[report.risk_level] : null;

  return (
    <div style={{ padding: 16 }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card
            title={
              <Space>
                <ExperimentOutlined />
                AI投研
              </Space>
            }
            extra={
              apiStatus ? (
                <Tag color="green">{apiStatus.provider}</Tag>
              ) : (
                <Tag color="orange">API未连接</Tag>
              )
            }
          >
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Select
                value={selectedStock.symbol}
                style={{ width: '100%' }}
                onChange={setSelectedSymbol}
                options={appState.stocks.map(stock => ({
                  value: stock.symbol,
                  label: `${stock.name} (${stock.symbol})`
                }))}
              />

              <Row gutter={12}>
                <Col span={12}>
                  <Statistic
                    title="当前价格"
                    prefix="$"
                    value={selectedStock.currentPrice}
                    precision={2}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="涨跌幅"
                    value={selectedStock.changePercent}
                    precision={2}
                    suffix="%"
                    valueStyle={{ color: selectedStock.changePercent >= 0 ? '#cf1322' : '#389e0d' }}
                  />
                </Col>
              </Row>

              <div>
                <Text type="secondary">关联内容</Text>
                <List
                  size="small"
                  dataSource={relatedPosts}
                  locale={{ emptyText: '暂无关联内容' }}
                  renderItem={post => (
                    <List.Item>
                      <Text ellipsis>{post.title}</Text>
                    </List.Item>
                  )}
                />
              </div>

              <TextArea
                value={question}
                onChange={event => setQuestion(event.target.value)}
                rows={4}
                placeholder="可选：输入你关心的问题，例如估值、风险事件、财报变化"
              />

              <Button
                type="primary"
                block
                icon={loading ? <ReloadOutlined spin /> : <BulbOutlined />}
                loading={loading}
                onClick={handleAnalyze}
              >
                生成投研报告
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={15}>
          {!report ? (
            <Card>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="选择股票后生成云模型投研报告"
              />
            </Card>
          ) : (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Card
                title={
                  <Space>
                    <LineChartOutlined />
                    {selectedStock.name} 投研摘要
                  </Space>
                }
                extra={<Tag color="blue">{report.model}</Tag>}
              >
                <Paragraph style={{ fontSize: 16 }}>{report.executive_summary}</Paragraph>
                <Row gutter={[16, 16]}>
                  <Col xs={24} md={8}>
                    <Statistic
                      title="情绪"
                      value={sentiment?.text}
                      valueStyle={{ color: sentiment?.color }}
                    />
                  </Col>
                  <Col xs={24} md={8}>
                    <Statistic
                      title="情绪分"
                      value={report.sentiment_score}
                      precision={2}
                    />
                    <Progress
                      percent={Math.round((report.sentiment_score + 1) * 50)}
                      showInfo={false}
                      strokeColor={sentiment?.color}
                    />
                  </Col>
                  <Col xs={24} md={8}>
                    <Statistic
                      title="风险等级"
                      value={risk?.text}
                      valueStyle={{ color: risk?.color }}
                    />
                  </Col>
                </Row>
              </Card>

              <Row gutter={[16, 16]}>
                <Col xs={24} md={12}>
                  <Card title={<Space><BulbOutlined />催化因素</Space>}>
                    <List
                      dataSource={report.catalysts}
                      renderItem={item => <List.Item>{item}</List.Item>}
                    />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card title={<Space><WarningOutlined />主要风险</Space>}>
                    <List
                      dataSource={report.risks}
                      renderItem={item => <List.Item>{item}</List.Item>}
                    />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card title={<Space><ApiOutlined />观察清单</Space>}>
                    <List
                      dataSource={report.watch_items}
                      renderItem={item => <List.Item>{item}</List.Item>}
                    />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card title={<Space><ExperimentOutlined />追问方向</Space>}>
                    <List
                      dataSource={report.suggested_questions}
                      renderItem={item => <List.Item>{item}</List.Item>}
                    />
                  </Card>
                </Col>
              </Row>

              <Alert
                type="info"
                showIcon
                message={report.disclaimer}
              />
            </Space>
          )}
        </Col>
      </Row>
    </div>
  );
};

export default AiResearch;
