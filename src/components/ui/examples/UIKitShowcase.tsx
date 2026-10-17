import React, { useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  Modal,
  Skeleton,
  Spinner,
  TabPanel,
  Tabs,
  ToastProvider,
  Tooltip,
  useToast,
} from '..';
import '../ui.css';

/**
 * UI Kit 使用示例（演示组件，不接入业务路由）。
 *
 * 临时预览方式：在任意已有页面内挂载 <UIKitShowcase />；
 * 各区块演示的是真实使用姿态，可直接复制到业务代码。
 */

function InteractiveSection() {
  const toast = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [nickname, setNickname] = useState('');
  const [nicknameError, setNicknameError] = useState<string | undefined>(undefined);
  const initialFocusRef = useRef<HTMLButtonElement | null>(null);

  const submit = () => {
    if (!nickname.trim()) {
      setNicknameError('昵称不能为空');
      return;
    }
    setNicknameError(undefined);
    setSubmitting(true);
    // 演示异步提交的加载态与完成通知。
    window.setTimeout(() => {
      setSubmitting(false);
      toast.success('已保存', '昵称将在下次登录时生效');
      setModalOpen(false);
    }, 800);
  };

  return (
    <Card padding="lg">
      <h3 style={{ marginBottom: 12 }}>交互组件</h3>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <Tooltip content="主按钮：每个视图内最多一个">
          <Button onClick={() => toast.success('已提交', '数据已同步到服务器')}>提交</Button>
        </Tooltip>
        <Button variant="secondary" onClick={() => setModalOpen(true)}>
          表单弹窗
        </Button>
        <Button variant="ghost" loading={submitting} onClick={submit}>
          幽灵按钮（可加载）
        </Button>
        <Button variant="danger" onClick={() => setConfirmOpen(true)}>
          删除数据源
        </Button>
        <Tooltip content="刷新行情缓存">
          <IconButton ariaLabel="刷新" variant="secondary" onClick={() => toast.info('已刷新')}>
            ⟳
          </IconButton>
        </Tooltip>
        <Spinner label="行情加载中" />
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Badge tone="positive" dot>已连接</Badge>
        <Badge tone="negative">配额不足</Badge>
        <Badge tone="warning">延迟偏高</Badge>
        <Badge tone="accent">Beta</Badge>
        <Badge tone="neutral">v1.0</Badge>
      </div>

      {/* 表单弹窗：初始聚焦确认按钮；Enter 直提交；加载态防重复触发 */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="编辑昵称"
        initialFocusRef={initialFocusRef}
        footer={
          <>
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              取消
            </Button>
            <Button ref={initialFocusRef} loading={submitting} onClick={submit}>
              保存
            </Button>
          </>
        }
      >
        <Input
          label="昵称"
          required
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          error={nicknameError}
          hint="仅用于社区展示，不影响账号"
          placeholder="输入昵称"
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
          }}
        />
      </Modal>

      {/* 确认弹窗：无 title 的 aria-label 形态 + 危险操作文案 */}
      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        size="sm"
        ariaLabel="确认删除"
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirmOpen(false);
                toast.error('已删除', '数据源与相关缓存一并移除');
              }}
            >
              确认删除
            </Button>
          </>
        }
      >
        删除后该数据源的历史数据不可恢复，确认继续？
      </Modal>
    </Card>
  );
}

function DataSection() {
  const [loading, setLoading] = useState(true);
  const tabs = [
    { key: 'overview', label: '概览' },
    { key: 'quotes', label: '行情', badge: 12 },
    { key: 'notes', label: '纪要', disabled: true },
  ];
  const [tab, setTab] = useState('overview');

  return (
    <Card padding="lg">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3>数据展示</h3>
        <Button size="sm" variant="secondary" onClick={() => setLoading((v) => !v)}>
          {loading ? '显示数据' : '显示骨架屏'}
        </Button>
      </div>

      {loading && (
        <div style={{ marginTop: 14 }}>
          <Skeleton variant="text" lines={3} srLabel="研报数据加载中" />
          <div style={{ height: 12 }} />
          <Skeleton variant="rectangular" height={96} />
        </div>
      )}

      <Tabs items={tabs} activeKey={tab} onChange={setTab} ariaLabel="数据区">
        {!loading && (
          <>
            <TabPanel tabKey="overview">
              <p>概览面板：TabPanel 自动接线 aria-controls / aria-labelledby。</p>
            </TabPanel>
            <TabPanel tabKey="quotes">
              <p>行情面板：keepMounted 可让面板保持挂载以保留内部状态。</p>
            </TabPanel>
          </>
        )}
      </Tabs>

      {!loading && (
        <div style={{ marginTop: 14 }}>
          <EmptyState
            title="暂无自选标的"
            description="从行情列表或搜索结果中加入自选，这里会汇总展示它们的关键指标。"
            action={<Button size="sm" variant="secondary">去添加自选</Button>}
          />
        </div>
      )}
    </Card>
  );
}

function FormSection() {
  const toast = useToast();
  const [amount, setAmount] = useState('');
  const amountError = amount !== '' && Number.isNaN(Number(amount)) ? '请输入有效数值' : undefined;

  return (
    <Card padding="lg">
      <h3 style={{ marginBottom: 12 }}>表单字段</h3>
      <div style={{ display: 'grid', gap: 14, maxWidth: 420 }}>
        <Input
          label="预警金额"
          required
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          error={amountError}
          prefix="¥"
          suffix="CNY"
          hint="触发后将通过站内通知与 Webhook 推送"
          placeholder="100000"
        />
        <Input label="禁用示例" value="服务器统一配置" disabled />
        <Button
          block
          disabled={!!amountError || amount === ''}
          onClick={() => toast.info('预警已创建', `阈值 ¥${amount}`)}
        >
          创建预警
        </Button>
      </div>
    </Card>
  );
}

function Showcase() {
  return (
    <ToastProvider>
      <div style={{ display: 'grid', gap: 16, padding: 20 }}>
        <InteractiveSection />
        <DataSection />
        <FormSection />
      </div>
    </ToastProvider>
  );
}

export default Showcase;
