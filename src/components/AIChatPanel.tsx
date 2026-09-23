import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Avatar,
  Button,
  Card,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  BulbOutlined,
  CloseOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
  ClearOutlined,
  ThunderboltOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { useModuleContext, ModuleContextData } from '../contexts/ModuleContext';
import { runOrchestratorChat, streamToolResearch } from '../services/agentService';
import type { OrchestratorReasoningStep, ToolStep } from '../services/agentService';
import { isResearchMessage } from '../utils/chatRouting';
import Markdown from './common/Markdown';
import ReasoningTrace, { formatToolLabel } from './common/ReasoningTrace';

const { Text, Paragraph } = Typography;

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  reasoning_trace?: OrchestratorReasoningStep[];
}

function buildContextSummary(ctx: ModuleContextData | null): string {
  if (!ctx) return '';
  const parts = [ctx.summary];
  if (ctx.data) {
    const keys = Object.keys(ctx.data);
    if (keys.length <= 3) {
      for (const k of keys) {
        const v = ctx.data[k];
        if (v !== null && v !== undefined && typeof v !== 'object') {
          parts.push(`${k}: ${v}`);
        }
      }
    }
  }
  return parts.filter(Boolean).join('\n');
}

interface AIChatPanelProps {
  open: boolean;
  onClose: () => void;
}

const AIChatPanel: React.FC<AIChatPanelProps> = ({ open, onClose }) => {
  const { currentContext } = useModuleContext();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [streamingTools, setStreamingTools] = useState<ToolStep[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);

  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [open]);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const sendMessage = useCallback(async (rawInput?: string) => {
    // 推荐问题点击时直接传入文本，不能依赖 setInput 的异步状态更新；
    // 否则发送函数可能捕获到旧的空 input，后端会收到 message: ""。
    const trimmed = (rawInput ?? input).trim();
    if (!trimmed || loading) return;
    setInput('');
    const userMsg: ChatMessage = { role: 'user', content: trimmed };
    setMessages(prev => [...prev, userMsg]);

    setLoading(true);

    const history = messages.slice(-6).map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
    }));
    // 模块上下文单独传入 context_hint，避免把上下文关键词混进当前问题而污染意图识别。
    const contextSummary = buildContextSummary(currentContext);
    // 当前模块的标的：研究类追问（消息里无显式 ticker，不会被路由去研究 Loop）时，让 tool-agent
    // 知道该查哪只股票（如正看 AAPL 时问「估值贵吗」）。
    const ctxSymbol =
      typeof currentContext?.data?.symbol === 'string' ? (currentContext.data.symbol as string) : '';
    const ctxName =
      typeof currentContext?.data?.name === 'string' ? (currentContext.data.name as string) : '';

    // ① 所有研究类问题统一走 SSE 研究路由：即使没有当前模块标的，也让后端自己解析
    //    中文股票名/多股比较并展示真实取数进度。此前只有「已有标的 + 研究关键词」才走这里，
    //    「宁德时代和比亚迪更偏向谁」会意外落到普通 orchestrator，体验和证据链都不稳定。
    //    onFallback/onError 时再回退 ② 非流式 orchestrator。
    if (isResearchMessage(trimmed)) {
      setStreamingTools([]);
      const streamed = await new Promise<boolean>((resolve) => {
        streamToolResearch(
          {
            message: trimmed,
            symbol: ctxSymbol,
            name: ctxName,
            ...(contextSummary ? { context_hint: contextSummary } : {}),
          },
          {
            onToolStart: (tool) => setStreamingTools(prev => [...prev, { tool }]),
            onToolResult: (tool, ok, summary, references) =>
              setStreamingTools(prev =>
                prev.map(s => (s.tool === tool && s.ok === undefined ? { ...s, ok, summary, references } : s)),
              ),
            onFinal: (answer, toolTrace) => {
              setMessages(prev => [...prev, {
                role: 'assistant',
                content: answer,
                reasoning_trace: toolTrace.map((t): OrchestratorReasoningStep => ({
                  phase: 'tool',
                  title: `调用 ${t.tool}`,
                  detail: t.summary || '',
                  status: t.ok ? 'done' : 'error',
                })),
              }]);
              resolve(true);
            },
            onFallback: () => resolve(false),
            onError: () => resolve(false),
          },
        );
      });
      setStreamingTools([]);
      if (streamed) {
        setLoading(false);
        return;
      }
      // 未流式成功 → 落到下方非流式 orchestrator 兜底。
    }

    try {
      const resp = await runOrchestratorChat({
        message: trimmed,
        ...(contextSummary ? { context_hint: contextSummary } : {}),
        history,
        engine: 'deepfocus',
        mode: 'research',
        reasoning_mode: 'thinking',
        locale: 'zh-CN',
        ...(ctxSymbol ? { stock: { symbol: ctxSymbol, name: ctxName } } : {}),
      });

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: resp.content,
        reasoning_trace: resp.reasoning_trace,
      }]);
    } catch (err: any) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: err.message || 'AI 服务暂时不可用，请稍后重试。',
      }]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, messages, currentContext]);

  const clearChat = useCallback(() => {
    setMessages([]);
    setStreamingTools([]);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  const ctxSummary = buildContextSummary(currentContext);
  const streamingReferences = Array.from(new Map(
    streamingTools.flatMap(tool => tool.references || [])
      .filter(reference => reference && reference.title)
      .map(reference => [
        reference.id || `${reference.category || ''}:${reference.title}:${reference.source || ''}`,
        reference,
      ]),
  ).values()).slice(-6);

  const suggestions = currentContext ? [
    `分析当前${currentContext.title}的主要信号`,
    `这些指标意味着什么？`,
    `当前市场环境下的投资建议`,
  ] : [
    '介绍一下 DeepFocus 的功能',
    '如何使用 Agent 分析？',
    '帮我分析当前市场',
  ];

  const sendSuggestion = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    void sendMessage(trimmed);
  }, [sendMessage]);

  if (!open) return null;

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      right: 0,
      width: 400,
      height: '100vh',
      background: 'var(--surface)',
      boxShadow: '-4px 0 20px rgba(0,0,0,0.3)',
      zIndex: 1050,
      display: 'flex',
      flexDirection: 'column',
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '12px 16px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--surface-muted)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <RobotOutlined style={{ fontSize: 18, color: 'var(--accent)' }} />
          <Text strong style={{ fontSize: 15 }}>AI 投研助手</Text>
        </div>
        <Space>
          <Tooltip title="清空对话">
            <Button size="small" icon={<ClearOutlined />} onClick={clearChat} />
          </Tooltip>
          <Button size="small" icon={<CloseOutlined />} onClick={onClose} />
        </Space>
      </div>

      {currentContext && (
        <div style={{
          padding: '8px 16px',
          background: 'var(--accent-soft)',
          borderBottom: '1px solid color-mix(in srgb, var(--accent) 18%, var(--border))',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}>
          <BulbOutlined style={{ color: 'var(--accent)', fontSize: 12 }} />
          <Text style={{ fontSize: 12, color: 'var(--accent)' }}>
            上下文已注入：{currentContext.title}
          </Text>
          {currentContext.data && Object.keys(currentContext.data).length > 0 && (
            <Tag
              style={{
                fontSize: 10,
                lineHeight: '16px',
                margin: 0,
                color: 'var(--accent)',
                borderColor: 'color-mix(in srgb, var(--accent) 32%, var(--border))',
                background: 'var(--accent-soft)',
              }}
            >
              {Object.keys(currentContext.data).length} 项数据
            </Tag>
          )}
        </div>
      )}

      <div ref={containerRef} style={{
        flex: 1,
        overflow: 'auto',
        padding: '12px 16px',
      }}>
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <RobotOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
            <Paragraph type="secondary" style={{ marginBottom: 16 }}>
              结合公开数据与稻草财经内容，给你有依据的投研回答。
            </Paragraph>
            {currentContext && (
              <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 16 }}>
                已加载「{currentContext.title}」的上下文数据，AI 将基于当前页面信息给出回答。
              </Paragraph>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
              {suggestions.map((sug, i) => (
                <Button
                  key={i}
                  type="default"
                  size="small"
                  onClick={() => sendSuggestion(sug)}
                  style={{ maxWidth: 280 }}
                >
                  {sug}
                </Button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  gap: 10,
                  marginBottom: 16,
                  flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                }}
              >
                <Avatar
                  size={32}
                  icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  style={{
                    backgroundColor: msg.role === 'user' ? 'var(--info)' : 'var(--accent)',
                    flexShrink: 0,
                  }}
                />
                <div style={{
                  maxWidth: '80%',
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: msg.role === 'user' ? 'var(--info)' : 'var(--surface-muted)',
                  color: msg.role === 'user' ? '#ffffff' : 'var(--text)',
                  fontSize: 13,
                  lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {msg.role === 'assistant'
                    ? <Markdown content={msg.content} />
                    : msg.content}
                  {msg.role === 'assistant' && msg.reasoning_trace && msg.reasoning_trace.length > 0 && (
                    <ReasoningTrace steps={msg.reasoning_trace} />
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <Avatar size={32} icon={<RobotOutlined />} style={{ backgroundColor: 'var(--accent)', flexShrink: 0 }} />
                {streamingTools.length > 0 ? (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', paddingTop: 4 }}>
                    <div style={{ marginBottom: 4 }}>
                      <ThunderboltOutlined style={{ marginRight: 4 }} />正在核对公开行情与站内资料…
                    </div>
                    {streamingTools.map((s, i) => (
                      <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 2 }}>
                        <span style={{ flexShrink: 0 }}>
                          {s.ok === undefined ? (
                            <LoadingOutlined style={{ color: 'var(--info)' }} />
                          ) : s.ok ? (
                            <span style={{ color: 'var(--positive)' }}>✓</span>
                          ) : (
                            <span style={{ color: 'var(--negative)' }}>✕</span>
                          )}
                        </span>
                        <span>
                          {formatToolLabel(s.tool)}
                          {s.summary ? ` — ${s.summary}` : ''}
                        </span>
                      </div>
                    ))}
                    {streamingReferences.length > 0 && (
                      <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--border)' }}>
                        <div style={{ marginBottom: 4, color: 'var(--text-secondary)' }}>已命中的资料</div>
                        {streamingReferences.map((reference, i) => (
                          <div key={`${reference.id || reference.title}-${i}`} title={reference.detail || reference.title} style={{ display: 'flex', gap: 5, marginBottom: 3, lineHeight: 1.45 }}>
                            <Tag color="blue" style={{ margin: 0, fontSize: 10, lineHeight: '17px' }}>{reference.category || '资料'}</Tag>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{reference.title}</span>
                            {(reference.source || reference.published_at) && <Text type="secondary" style={{ flexShrink: 0, fontSize: 10 }}>{[reference.source, reference.published_at?.slice(0, 10)].filter(Boolean).join(' · ')}</Text>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <Spin size="small" />
                )}
              </div>
            )}
          </>
        )}
      </div>

      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface-muted)',
      }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={currentContext ? `基于「${currentContext.title}」提问...` : '输入您的问题...'}
            disabled={loading}
            maxLength={500}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={() => void sendMessage()}
            loading={loading}
            disabled={!input.trim()}
          />
        </Space.Compact>
      </div>
    </div>
  );
};

export default React.memo(AIChatPanel);
