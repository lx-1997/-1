import React, { useCallback, useRef, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CloseCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import DataCenter from './DataCenter';
import './PositionMonitor.css';
import {
  getPositions,
  createPosition,
  updatePosition,
  deletePosition,
  closePosition,
  refreshPrices,
  PositionRecord,
  PositionCreateRequest,
} from '../services/riskService';

const { Text, Title } = Typography;

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const fmtUsd = (v: number): string => usdFormat.format(v);
const fmtNum = (v: number): string => numberFormat.format(v);
const fmtPct = (v: number): string => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

interface PositionFormValues {
  symbol: string;
  name: string;
  market: string;
  asset_class: string;
  direction: string;
  entry_price: number;
  quantity: number;
  stop_loss?: number;
  take_profit?: number;
  sector: string;
  strategy: string;
  notes: string;
}

function PositionMonitor() {
  const { message } = AntdApp.useApp();
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPosition, setEditingPosition] = useState<PositionRecord | null>(null);
  const [closeModalOpen, setCloseModalOpen] = useState(false);
  const [closingPosition, setClosingPosition] = useState<PositionRecord | null>(null);
  const [form] = Form.useForm<PositionFormValues>();
  const [closeForm] = Form.useForm<{ exit_price: number; exit_reason: string }>();
  // DataCenter 通过 renderContent 把它内部的 loadData 作为 refresh 注入，
  // 这里用 ref 捕获，供作用域外的各 mutation handler 在成功后刷新表格与统计卡。
  const refreshRef = useRef<(() => void) | null>(null);

  const handleCreate = useCallback(() => {
    setEditingPosition(null);
    form.resetFields();
    form.setFieldsValue({
      market: 'US',
      asset_class: 'equity',
      direction: 'long',
    });
    setModalOpen(true);
  }, [form]);

  const handleEdit = useCallback((pos: PositionRecord) => {
    setEditingPosition(pos);
    form.setFieldsValue({
      symbol: pos.symbol,
      name: pos.name,
      market: pos.market,
      asset_class: pos.asset_class,
      direction: pos.direction,
      entry_price: pos.entry_price,
      quantity: pos.quantity,
      stop_loss: pos.stop_loss || undefined,
      take_profit: pos.take_profit || undefined,
      sector: pos.sector,
      strategy: pos.strategy,
      notes: pos.notes,
    });
    setModalOpen(true);
  }, [form]);

  const handleClosePosition = useCallback((pos: PositionRecord) => {
    setClosingPosition(pos);
    closeForm.resetFields();
    closeForm.setFieldsValue({ exit_price: pos.current_price || pos.entry_price });
    setCloseModalOpen(true);
  }, [closeForm]);

  const handleSubmit = useCallback(async () => {
    try {
      const values = await form.validateFields();
      if (editingPosition) {
        await updatePosition(editingPosition.id, values);
        message.success('头寸已更新');
      } else {
        await createPosition(values as PositionCreateRequest);
        message.success('头寸已创建');
      }
      setModalOpen(false);
      form.resetFields();
      refreshRef.current?.();
    } catch (err: any) {
      message.error(err.message || '操作失败');
    }
  }, [form, editingPosition, message]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deletePosition(id);
      message.success('头寸已删除');
      refreshRef.current?.();
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  }, [message]);

  const handleClose = useCallback(async () => {
    if (!closingPosition) return;
    try {
      const values = await closeForm.validateFields();
      await closePosition(closingPosition.id, values.exit_price, values.exit_reason);
      message.success('头寸已平仓');
      setCloseModalOpen(false);
      closeForm.resetFields();
      refreshRef.current?.();
    } catch (err: any) {
      message.error(err.message || '平仓失败');
    }
  }, [closingPosition, closeForm, message]);

  const handleRefreshPrices = useCallback(async () => {
    try {
      const result = await refreshPrices();
      message.success(`已刷新 ${result.updated_count} 个头寸的实时价格`);
      refreshRef.current?.();
    } catch (err: any) {
      message.error(err.message || '刷新失败');
    }
  }, [message]);

  const columns = [
    {
      title: '代码',
      dataIndex: 'symbol',
      key: 'symbol',
      width: 90,
      render: (v: string) => <Text strong>{v}</Text>,
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 90,
    },
    {
      title: '方向',
      dataIndex: 'direction',
      key: 'direction',
      width: 60,
      render: (d: string) => (
        <Tag color={d === 'long' ? 'green' : 'red'}>
          {d === 'long' ? '多' : '空'}
        </Tag>
      ),
    },
    {
      title: '入场价',
      dataIndex: 'entry_price',
      key: 'entry_price',
      width: 80,
      render: (v: number) => fmtNum(v),
    },
    {
      title: '现价',
      dataIndex: 'current_price',
      key: 'current_price',
      width: 80,
      render: (v: number | null) => v ? fmtNum(v) : '--',
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      key: 'quantity',
      width: 60,
      render: (v: number) => fmtNum(v),
    },
    {
      title: '市值',
      dataIndex: 'notional_value',
      key: 'notional_value',
      width: 100,
      render: (v: number) => v ? fmtUsd(v) : '--',
    },
    {
      title: '未实现盈亏',
      dataIndex: 'unrealized_pnl',
      key: 'unrealized_pnl',
      width: 130,
      render: (v: number, record: PositionRecord) => (
        <span style={{ color: v >= 0 ? '#52c41a' : '#ff4d4f', fontWeight: 500 }}>
          {fmtUsd(v)} ({fmtPct(record.unrealized_pnl_pct)})
        </span>
      ),
    },
    {
      title: '止损/止盈',
      key: 'sl_tp',
      width: 120,
      render: (_: any, record: PositionRecord) => (
        <Space className="position-monitor-tag-cell" wrap size={[4, 4]}>
          {record.stop_loss && <Tag color="red">SL: {record.stop_loss}</Tag>}
          {record.take_profit && <Tag color="green">TP: {record.take_profit}</Tag>}
        </Space>
      ),
    },
    {
      title: '行业',
      dataIndex: 'sector',
      key: 'sector',
      width: 80,
    },
    {
      title: '操作',
      key: 'actions',
      width: 118,
      render: (_: any, record: PositionRecord) => (
        record.status === 'open' ? (
          <Space className="position-monitor-actions" size={4}>
            <Tooltip title="编辑">
              <Button
                size="small"
                aria-label="编辑头寸"
                icon={<EditOutlined />}
                onClick={() => handleEdit(record)}
              />
            </Tooltip>
            <Tooltip title="平仓">
              <Button
                size="small"
                aria-label="平仓"
                icon={<CloseCircleOutlined />}
                onClick={() => handleClosePosition(record)}
              />
            </Tooltip>
            <Popconfirm
              title="确定删除此头寸？"
              onConfirm={() => handleDelete(record.id)}
              okText="删除"
              cancelText="取消"
            >
              <Tooltip title="删除">
                <Button
                  size="small"
                  danger
                  aria-label="删除头寸"
                  icon={<DeleteOutlined />}
                />
              </Tooltip>
            </Popconfirm>
          </Space>
        ) : (
          <Tag>已平仓</Tag>
        )
      ),
    },
  ];

  const renderContent = useCallback((data: PositionRecord[], refresh: () => void) => {
    refreshRef.current = refresh;
    const openPositions = data.filter(p => p.status === 'open');
    const closedPositions = data.filter(p => p.status === 'closed');
    const totalPnl = openPositions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);
    const totalValue = openPositions.reduce((sum, p) => sum + (p.notional_value || 0), 0);

    return (
      <div>
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={6}>
            <Card size="small">
              <Statistic
                title="持仓数"
                value={openPositions.length}
                suffix={`/ ${data.length}`}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small">
              <Statistic
                title="总市值"
                value={totalValue}
                precision={0}
                prefix="$"
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small">
              <Statistic
                title="未实现盈亏"
                value={totalPnl}
                precision={0}
                prefix="$"
                valueStyle={{ color: totalPnl >= 0 ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small">
              <Statistic
                title="已平仓"
                value={closedPositions.length}
              />
            </Card>
          </Col>
        </Row>

        <Table
          className="position-monitor-table"
          dataSource={openPositions}
          columns={columns}
          rowKey="id"
          size="small"
          tableLayout="fixed"
          scroll={{ x: 1058 }}
          pagination={{ pageSize: 20 }}
        />
      </div>
    );
  }, [columns, handleEdit, handleClosePosition, handleDelete]);

  const extra = (
    <>
      <Button icon={<ReloadOutlined />} onClick={handleRefreshPrices}>
        刷新价格
      </Button>
      <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
        新建头寸
      </Button>
    </>
  );

  return (
    <>
      <DataCenter<PositionRecord[]>
        title="持仓监控"
        subtitle="实时跟踪头寸变动、盈亏与风险收益比"
        fetchData={() => getPositions()}
        renderContent={(data, refresh) => renderContent(data, refresh)}
        emptyText="暂无持仓，请新建头寸开始跟踪"
        className="position-monitor-page"
        refreshLabel="刷新持仓"
        extra={extra}
      />

      <Modal
        title={editingPosition ? '编辑头寸' : '新建头寸'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText={editingPosition ? '保存' : '创建'}
        cancelText="取消"
        width={640}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="symbol" label="代码" rules={[{ required: true, message: '请输入交易代码' }]}>
                <Input placeholder="AAPL, 0700.HK, 600519.SH" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="name" label="名称">
                <Input placeholder="公司名称" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="market" label="市场">
                <Select>
                  <Select.Option value="US">美股</Select.Option>
                  <Select.Option value="HK">港股</Select.Option>
                  <Select.Option value="CN">A股</Select.Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="direction" label="方向">
                <Select>
                  <Select.Option value="long">多头</Select.Option>
                  <Select.Option value="short">空头</Select.Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="asset_class" label="资产类别">
                <Select>
                  <Select.Option value="equity">股票</Select.Option>
                  <Select.Option value="option">期权</Select.Option>
                  <Select.Option value="future">期货</Select.Option>
                  <Select.Option value="forex">外汇</Select.Option>
                  <Select.Option value="bond">债券</Select.Option>
                  <Select.Option value="crypto">加密货币</Select.Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="sector" label="行业">
                <Input placeholder="科技、金融、医药..." />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="entry_price" label="入场价" rules={[{ required: true, message: '请输入入场价' }]}>
                <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="quantity" label="数量" rules={[{ required: true, message: '请输入数量' }]}>
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="strategy" label="策略">
                <Input placeholder="趋势跟踪、均值回归..." />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="stop_loss" label="止损价">
                <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="take_profit" label="止盈价">
                <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="平仓"
        open={closeModalOpen}
        onOk={handleClose}
        onCancel={() => setCloseModalOpen(false)}
        okText="确认平仓"
        cancelText="取消"
        destroyOnHidden
      >
        {closingPosition && (
          <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="代码">{closingPosition.symbol}</Descriptions.Item>
            <Descriptions.Item label="方向">
              {closingPosition.direction === 'long' ? '多头' : '空头'}
            </Descriptions.Item>
            <Descriptions.Item label="入场价">{closingPosition.entry_price}</Descriptions.Item>
            <Descriptions.Item label="数量">{closingPosition.quantity}</Descriptions.Item>
          </Descriptions>
        )}
        <Form form={closeForm} layout="vertical">
          <Form.Item name="exit_price" label="平仓价" rules={[{ required: true, message: '请输入平仓价' }]}>
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="exit_reason" label="平仓原因">
            <Input.TextArea rows={2} placeholder="止盈/止损/策略调整..." />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export default PositionMonitor;
