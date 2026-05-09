import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
  message
} from 'antd';
import {
  AuditOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import {
  AgentRuntimeHealth,
  InvestmentTaskCreate,
  InvestmentTaskRecord,
  cancelAgentTask,
  createAgentTask,
  getAgentHealth,
  listAgentTasks,
  retryAgentTask
} from '../services/agentTaskService';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface InvestorAgentCenterProps {
  appState: AppState;
}

const statusMeta: Record<string, { color: string; text: string; badge: 'default' | 'processing' | 'success' | 'error' | 'warning' }> = {
  pending: { color: 'default', text: '排队中', badge: 'default' },
  running: { color: 'processing', text: '执行中', badge: 'processing' },
  waiting_approval: { color: 'warning', text: '待确认', badge: 'warning' },
  failed: { color: 'error', text: '失败', badge: 'error' },
  completed: { color: 'success', text: '完成', badge: 'success' },
  cancelled: { color: 'default', text: '已取消', badge: 'default' }
};

const decisionMeta: Record<string, { color: string; text: string }> = {
  avoid: { color: 'red', text: '暂避' },
  watch: { color: 'blue', text: '观察' },
  research_more: { color: 'gold', text: '继续研究' },
  candidate: { color: 'green', text: '候选机会' }
};

const InvestorAgentCenter: React.FC<InvestorAgentCenterProps> = ({ appState }) => {
  const [health, setHealth] = useState<AgentRuntimeHealth | null>(null);
  const [tasks, setTasks] = useState<InvestmentTaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<InvestmentTaskCreate>({
    title: '多 Agent 投研：特斯拉是否值得继续研究',
    symbol: appState.stocks[0]?.symbol,
    asset_name: appState.stocks[0]?.name,
    task_type: 'investment_research',
    horizon: '1-4周',
    investor_profile: '稳健',
    objective: '判断是否值得进入观察名单，并给出风险控制和验证清单。',
    context: '请结合当前社区内容、财报变化、市场情绪和风险事件，输出投资者能看懂的结论。',
    priority: 3
  });

  const selectedTask = useMemo(
    () => tasks.find(task => task.id === selectedTaskId) || tasks[0] || null,
    [tasks, selectedTaskId]
  );

  const loadData = async () => {
    setLoading(true);
    try {
      const [nextHealth, nextTasks] = await Promise.all([
        getAgentHealth(),
        listAgentTasks()
      ]);
      setHealth(nextHealth);
      setTasks(nextTasks);
      if (!selectedTaskId && nextTasks.length > 0) {
        setSelectedTaskId(nextTasks[0].id);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '任务中心连接失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = window.setInterval(loadData, 3000);
    return () => window.clearInterval(timer);
  }, []);

  const updateStock = (symbol: string) => {
    const stock = appState.stocks.find(item => item.symbol === symbol);
    setForm(prev => ({
      ...prev,
      symbol,
      asset_name: stock?.name || symbol,
      title: `多 Agent 投研：${stock?.name || symbol}是否值得继续研究`
    }));
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const created = await createAgentTask(form);
      setSelectedTaskId(created.id);
      await loadData();
      message.success('投研任务已进入 24h Agent 队列');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '创建任务失败');
    } finally {
      setCreating(false);
    }
  };

  const handleRetry = async (taskId: string) => {
    await retryAgentTask(taskId);
    await loadData();
  };

  const handleCancel = async (taskId: string) => {
    await cancelAgentTask(taskId);
    await loadData();
  };

  return (
    <div style={{ padding: 16 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} md={14}>
              <Title level={3} style={{ margin: 0 }}>24h 多 Agent 投研任务中心</Title>
              <Text type="secondary">任务排队、数据源检索、后台执行、日志留痕、结果复核。目标是提高投资决策质量，不承诺收益。</Text>
            </Col>
            <Col xs={24} md={10}>
              <Row gutter={12}>
                <Col span={6}><Statistic title="排队" value={health?.pending || 0} /></Col>
                <Col span={6}><Statistic title="执行" value={health?.running || 0} /></Col>
                <Col span={6}><Statistic title="完成" value={health?.completed || 0} /></Col>
                <Col span={6}><Statistic title="失败" value={health?.failed || 0} /></Col>
              </Row>
              <Tag color={health?.worker_running ? 'green' : 'red'} style={{ marginTop: 8 }}>
                {health?.worker_running ? 'worker 运行中' : 'worker 未运行'}
              </Tag>
            </Col>
          </Row>
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={8}>
            <Card title={<Space><PlayCircleOutlined />创建投研任务</Space>}>
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Select
                  value={form.symbol}
                  onChange={updateStock}
                  options={appState.stocks.map(stock => ({
                    value: stock.symbol,
                    label: `${stock.name} (${stock.symbol})`
                  }))}
                />
                <Input
                  value={form.title}
                  onChange={event => setForm(prev => ({ ...prev, title: event.target.value }))}
                  placeholder="任务标题"
                />
                <Row gutter={8}>
                  <Col span={12}>
                    <Select
                      value={form.task_type}
                      onChange={value => setForm(prev => ({ ...prev, task_type: value }))}
                      options={[
                        { value: 'investment_research', label: '个股投研' },
                        { value: 'portfolio_review', label: '组合复盘' },
                        { value: 'risk_review', label: '风险审查' },
                        { value: 'watchlist_monitor', label: '观察名单' }
                      ]}
                    />
                  </Col>
                  <Col span={12}>
                    <Select
                      value={form.investor_profile}
                      onChange={value => setForm(prev => ({ ...prev, investor_profile: value }))}
                      options={['保守', '稳健', '进取', '专业'].map(value => ({ value, label: value }))}
                    />
                  </Col>
                </Row>
                <Input
                  value={form.horizon}
                  onChange={event => setForm(prev => ({ ...prev, horizon: event.target.value }))}
                  placeholder="投资周期，例如 1-4周 / 3-6个月"
                />
                <TextArea
                  rows={3}
                  value={form.objective}
                  onChange={event => setForm(prev => ({ ...prev, objective: event.target.value }))}
                  placeholder="任务目标"
                />
                <TextArea
                  rows={6}
                  value={form.context}
                  onChange={event => setForm(prev => ({ ...prev, context: event.target.value }))}
                  placeholder="补充资料、限制条件、你的问题；数据源中心资料会自动检索"
                />
                <Alert
                  type="warning"
                  showIcon
                  message="系统会给出研究结论、风险和行动清单，但不会替你下单，也不会保证赚钱。"
                />
                <Button type="primary" block icon={<ThunderboltOutlined />} loading={creating} onClick={handleCreate}>
                  提交给多 Agent
                </Button>
              </Space>
            </Card>
          </Col>

          <Col xs={24} xl={16}>
            <Card
              title={<Space><ClockCircleOutlined />任务队列</Space>}
              extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={loadData}>刷新</Button>}
            >
              <Table
                size="small"
                rowKey="id"
                dataSource={tasks}
                pagination={{ pageSize: 6 }}
                onRow={record => ({ onClick: () => setSelectedTaskId(record.id) })}
                columns={[
                  {
                    title: '任务',
                    dataIndex: 'title',
                    render: (value, record) => (
                      <Space direction="vertical" size={0}>
                        <Text strong>{value}</Text>
                        <Text type="secondary">{record.symbol || '-'} · {record.assigned_agent || '-'}</Text>
                      </Space>
                    )
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 100,
                    render: status => (
                      <Badge status={statusMeta[status]?.badge || 'default'} text={statusMeta[status]?.text || status} />
                    )
                  },
                  {
                    title: '进度',
                    dataIndex: 'progress',
                    width: 160,
                    render: value => <Progress percent={value} size="small" />
                  },
                  {
                    title: '动作',
                    width: 130,
                    render: (_, record) => (
                      <Space>
                        {['failed', 'cancelled', 'completed'].includes(record.status) && (
                          <Button size="small" onClick={event => { event.stopPropagation(); handleRetry(record.id); }}>重跑</Button>
                        )}
                        {['pending', 'running'].includes(record.status) && (
                          <Button size="small" danger onClick={event => { event.stopPropagation(); handleCancel(record.id); }}>取消</Button>
                        )}
                      </Space>
                    )
                  }
                ]}
              />
            </Card>
          </Col>
        </Row>

        {selectedTask ? <TaskDetail task={selectedTask} /> : <Empty description="暂无任务" />}
      </Space>
    </div>
  );
};

const TaskDetail: React.FC<{ task: InvestmentTaskRecord }> = ({ task }) => {
  const meta = statusMeta[task.status] || statusMeta.pending;
  const result = task.result;
  const decision = result ? decisionMeta[result.decision] || decisionMeta.research_more : null;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={9}>
        <Card title={<Space><AuditOutlined />任务详情</Space>} extra={<Tag color={meta.color}>{meta.text}</Tag>}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="标的">{task.asset_name || task.symbol || '-'}</Descriptions.Item>
            <Descriptions.Item label="类型">{task.task_type}</Descriptions.Item>
            <Descriptions.Item label="优先级">{task.priority}</Descriptions.Item>
            <Descriptions.Item label="当前 Agent">{task.assigned_agent || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{new Date(task.created_at).toLocaleString()}</Descriptions.Item>
          </Descriptions>
          <Progress percent={task.progress} style={{ marginTop: 12 }} />
          {task.error && <Alert type="error" showIcon message={task.error} style={{ marginTop: 12 }} />}
        </Card>

        <Card title="执行日志" style={{ marginTop: 16 }}>
          <Timeline
            items={task.logs.map(log => ({
              color: log.agent === task.assigned_agent ? 'blue' : 'gray',
              children: (
                <Space direction="vertical" size={0}>
                  <Text strong>{log.agent}</Text>
                  <Text>{log.message}</Text>
                  <Text type="secondary">{new Date(log.timestamp).toLocaleString()}</Text>
                </Space>
              )
            }))}
          />
        </Card>
      </Col>

      <Col xs={24} xl={15}>
        {!result ? (
          <Card>
            <Empty description="任务完成后会生成投资者报告" />
          </Card>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card
              title={<Space><CheckCircleOutlined />投资者摘要</Space>}
              extra={<Tag color={decision?.color}>{decision?.text}</Tag>}
            >
              <Paragraph style={{ fontSize: 16 }}>{result.investor_summary}</Paragraph>
              <Row gutter={[16, 16]}>
                <Col xs={24} md={8}>
                  <Statistic title="置信度" value={result.confidence * 100} precision={0} suffix="%" />
                  <Progress percent={Math.round(result.confidence * 100)} showInfo={false} />
                </Col>
                <Col xs={24} md={16}>
                  <Alert type="info" showIcon message={result.plain_language_takeaway} />
                </Col>
              </Row>
            </Card>

            {result.evidence && result.evidence.length > 0 && (
              <Card title="证据来源">
                <List
                  size="small"
                  dataSource={result.evidence}
                  renderItem={item => (
                    <List.Item>
                      <Space direction="vertical" size={2}>
                        <Space wrap>
                          <Text strong>{item.title}</Text>
                          <Tag>{item.source}</Tag>
                          <Tag color="gold">{Math.round(item.credibility_score * 100)}%</Tag>
                          {item.tags?.map(tag => <Tag key={tag}>{tag}</Tag>)}
                        </Space>
                        <Text>{item.takeaway}</Text>
                        {item.url && <Text type="secondary">{item.url}</Text>}
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            )}

            <Card title="多 Agent 结论">
              <Steps
                direction="vertical"
                size="small"
                items={Object.entries(result.agent_findings || {}).map(([agent, findings]) => ({
                  title: agent,
                  description: (
                    <List
                      size="small"
                      dataSource={findings}
                      renderItem={item => <List.Item>{item}</List.Item>}
                    />
                  )
                }))}
              />
            </Card>

            <Row gutter={[16, 16]}>
              <Col xs={24} lg={12}>
                <Card title="情景推演">
                  <List
                    dataSource={result.scenarios}
                    renderItem={scenario => (
                      <List.Item>
                        <Space direction="vertical">
                          <Text strong>{scenario.case} · {scenario.probability}%</Text>
                          <Text>{scenario.thesis}</Text>
                          <Space wrap>{scenario.triggers.map(item => <Tag key={item}>{item}</Tag>)}</Space>
                        </Space>
                      </List.Item>
                    )}
                  />
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card title={<Space><SafetyCertificateOutlined />风险纪律</Space>}>
                  <List size="small" dataSource={result.risk_controls} renderItem={item => <List.Item>{item}</List.Item>} />
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card title="行动清单">
                  <List size="small" dataSource={result.action_plan} renderItem={item => <List.Item>{item}</List.Item>} />
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card title="反证清单">
                  <List size="small" dataSource={result.disconfirming_evidence} renderItem={item => <List.Item>{item}</List.Item>} />
                </Card>
              </Col>
            </Row>

            <Alert type="warning" showIcon message={result.disclaimer} />
          </Space>
        )}
      </Col>
    </Row>
  );
};

export default InvestorAgentCenter;
