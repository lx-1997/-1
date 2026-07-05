import React, { useEffect, useState } from 'react';
import { Modal, Button, Space, Typography, Input, message, Divider, Card, Row, Col } from 'antd';
import {
  ShareAltOutlined,
  WechatOutlined,
  WeiboOutlined,
  QqOutlined,
  LinkOutlined,
  CopyOutlined
} from '@ant-design/icons';
import { createShareSnapshot } from '../services/shareService';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

/** 通用可分享对象：帖子、AI 研报结论、圆桌纪要都收敛成这一种形状。 */
export interface ShareTarget {
  title: string;
  summary: string;
  /** 署名行，例如「作者：xxx」或「由 DeepFocus 投研工作台生成」。 */
  byline?: string;
  /** 可选的可访问链接；缺省时隐藏链接相关入口，社交按钮退化为「复制文案」。 */
  url?: string;
  /** 内容类型；'article' 时分享文案走「📰 文章」勾人格式（标识+钩子+点这看全文 CTA）。 */
  kind?: string;
}

interface LegacyPost {
  id: string;
  title: string;
  summary: string;
  author: {
    username: string;
  };
}

interface ShareModalProps {
  visible: boolean;
  onCancel: () => void;
  /** 旧调用方（社区帖子）继续用 post；新调用方用 content 传任意结论。 */
  post?: LegacyPost;
  content?: ShareTarget;
  /** 弹窗标题，默认「分享」。 */
  modalTitle?: string;
  /** 极简模式：只给「可复制文案 + 可跳转链接」，不展开 12 平台/生成链接等（文章分享用）。 */
  simple?: boolean;
}

// 终端暗色皮肤：Modal 走 portal 挂在 body 下、拿不到 .bbt 作用域里的变量，
// 故每个 var() 都带上与 FinancialTerminal.css 同值的回退色（终端琥珀黑板）。
const DARK_MODAL_CSS = `
.df-share-modal-dark .ant-modal-content{background:var(--elevated,#0a0d12);border:1px solid var(--line-2,#1c2530);color:var(--text-body,#e8ddc0);}
.df-share-modal-dark .ant-modal-header{background:transparent;}
.df-share-modal-dark .ant-modal-title{color:var(--amber,#ffb000);}
.df-share-modal-dark .ant-modal-close{color:var(--mute2,#9aa6b2);}
.df-share-modal-dark .ant-modal-close:hover{color:var(--text-strong,#ffffff);background:rgba(255,255,255,.08);}
.df-share-modal-dark .ant-typography{color:var(--text-body,#e8ddc0);}
.df-share-modal-dark h5.ant-typography{color:var(--amber-2,#ffce72);}
.df-share-modal-dark .ant-typography.ant-typography-secondary{color:var(--mute2,#9aa6b2);}
.df-share-modal-dark .ant-input,.df-share-modal-dark textarea.ant-input{background:var(--input-bg,#0c0d12);border-color:var(--line-2,#1c2530);color:var(--text-body,#e8ddc0);}
.df-share-modal-dark .ant-input::placeholder{color:var(--mute2,#9aa6b2);opacity:.8;}
.df-share-modal-dark .ant-input-affix-wrapper{background:var(--input-bg,#0c0d12);border-color:var(--line-2,#1c2530);}
.df-share-modal-dark .ant-input-affix-wrapper .ant-input{background:transparent;}
.df-share-modal-dark .ant-btn-default{background:transparent;border-color:var(--line-3,#2a3340);color:var(--text-soft,#cfd3da);}
.df-share-modal-dark .ant-btn-default:not(:disabled):hover{border-color:var(--amber,#ffb000);color:var(--amber,#ffb000);}
.df-share-modal-dark .ant-btn-primary{background:var(--accent,#ffb000);border-color:var(--accent,#ffb000);color:#000;}
.df-share-modal-dark .ant-btn-primary:not(:disabled):hover{background:var(--amber-2,#ffce72);border-color:var(--amber-2,#ffce72);color:#000;}
.df-share-modal-dark .ant-card{background:var(--panel,#07090d);border-color:var(--line-2,#1c2530);}
.df-share-modal-dark .ant-divider{border-color:var(--line-2,#1c2530);}
.df-share-modal-dark a{color:var(--blue,#6ab0ff);}
`;

const ShareModal: React.FC<ShareModalProps> = ({
  visible,
  onCancel,
  post,
  content,
  modalTitle,
  simple,
}) => {
  // 旧调用方（社区帖子）按需转换；不再伪造 https://deepfocus.com/post/ 链接（历史死代码，域名是错的）。
  const target: ShareTarget = content
    ?? (post ? { title: post.title, summary: post.summary, byline: `作者：${post.author.username}` } : { title: '', summary: '' });

  const [customMessage, setCustomMessage] = useState('');
  const [shareUrl, setShareUrl] = useState(target.url ?? '');
  const [generatingLink, setGeneratingLink] = useState(false);
  // 一旦有链接（自带或现场生成）即翻转为「带链接」模式：显示链接区、社交分享附带链接。
  const hasUrl = Boolean(shareUrl);

  // 目标切换（换一条结论分享）时同步链接，并清空上次自定义文案。
  useEffect(() => {
    setShareUrl(target.url ?? '');
    setCustomMessage('');
  }, [target.url, target.title, target.summary]);

  // 生成公开只读页：把结论存成后端快照，拿到免登录、可被搜索/转发的 URL。
  const handleGenerateLink = async () => {
    setGeneratingLink(true);
    try {
      const snapshot = await createShareSnapshot({
        title: target.title,
        summary: target.summary,
        byline: target.byline,
      });
      setShareUrl(snapshot.url);
      message.success('公开链接已生成');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '生成链接失败（需后端就绪）');
    } finally {
      setGeneratingLink(false);
    }
  };

  // 生成分享内容。标题/摘要/署名按非空拼接（空字段不留空行，避免重复/冗余），脚注统一带品牌归属。
  const generateShareContent = (platform: string) => {
    const baseContent = [target.title, target.summary, target.byline]
      .map(s => (s || '').trim())
      .filter(Boolean)
      .join('\n\n');
    const link = hasUrl ? shareUrl : '';

    // 文章分享：📰 文章标识 + 正文钩子(有则带) + 明确「点这看全文」CTA + 品牌脚注——
    // 让人一眼知道是文章、且勾起点开欲望。各平台统一用这套（文章分享主要走微信/复制）。
    if (target.kind === 'article') {
      const parts = [`📰 ${target.title}`];
      if ((target.summary || '').trim()) parts.push(target.summary.trim());
      parts.push('👉 点这看全文 · DeepFocus 金融数据');
      return `${parts.join('\n\n')}${link ? `\n${link}` : ''}`;
    }

    // 研报解读分享：📑 标识 + 一句话钩子 + 「登录看完整解读」CTA + 品牌脚注。分享的是我们的 AI 解读，非第三方原文。
    if (target.kind === 'report') {
      const parts = [`📑 研报速读丨${target.title}`];
      if ((target.summary || '').trim()) parts.push(target.summary.trim());
      parts.push('👉 登录看完整 AI 解读 · DeepFocus 金融数据');
      return `${parts.join('\n\n')}${link ? `\n${link}` : ''}`;
    }

    switch (platform) {
      case 'wechat':
        return `${baseContent}\n\n来自 DeepFocus 金融数据${link ? `\n${link}` : ''}`;
      case 'weibo':
        return `${baseContent}\n\n#DeepFocus# #投研#${link ? ` ${link}` : ''}`;
      case 'qq':
        return `${baseContent}\n\n分享自 DeepFocus${link ? `：${link}` : ''}`;
      default:
        return `${baseContent}${link ? `\n\n${link}` : ''}`.trim();
    }
  };

  const copyToClipboard = (text: string, okMsg: string) => {
    try {
      navigator.clipboard.writeText(text).then(() => {
        message.success(okMsg);
      }).catch(() => {
        message.error('复制失败，请手动复制');
      });
    } catch (error) {
      console.error('复制失败:', error);
      message.error('复制失败');
    }
  };

  // 分享到微信（无 Web 入口，统一走复制粘贴）
  const shareToWechat = () => {
    copyToClipboard(generateShareContent('wechat'), '已复制到剪贴板，可以粘贴到微信分享');
  };

  // 分享到微博
  const shareToWeibo = () => {
    const content = generateShareContent('weibo');
    if (!hasUrl) {
      copyToClipboard(content, '已复制微博文案，可直接粘贴发布');
      return;
    }
    const weiboUrl = `https://service.weibo.com/share/share.php?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(content)}`;
    window.open(weiboUrl, '_blank');
    message.success('正在跳转到微博分享页面');
  };

  // 分享到QQ
  const shareToQQ = () => {
    if (!hasUrl) {
      copyToClipboard(generateShareContent('qq'), '已复制文案，可直接粘贴分享');
      return;
    }
    const qqUrl = `https://connect.qq.com/widget/shareqq/index.html?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(target.title)}&summary=${encodeURIComponent(target.summary)}`;
    window.open(qqUrl, '_blank');
    message.success('正在跳转到QQ分享页面');
  };

  // 复制链接
  const copyLink = () => {
    copyToClipboard(shareUrl, '链接已复制到剪贴板');
  };

  // 复制完整内容（极简模式用带品牌行的 wechat 文案，与文本框展示一致）
  const copyContent = () => {
    copyToClipboard(customMessage || generateShareContent(simple ? 'wechat' : 'default'), '文案已复制到剪贴板');
  };

  const sharePlatforms = [
    {
      key: 'wechat',
      name: '微信',
      icon: <WechatOutlined style={{ color: '#07c160' }} />,
      color: '#07c160',
      action: shareToWechat
    },
    {
      key: 'weibo',
      name: '微博',
      icon: <WeiboOutlined style={{ color: '#e6162d' }} />,
      color: '#e6162d',
      action: shareToWeibo
    },
    {
      key: 'qq',
      name: 'QQ',
      icon: <QqOutlined style={{ color: '#12b7f5' }} />,
      color: '#12b7f5',
      action: shareToQQ
    }
  ];

  return (
    <Modal
      className="df-share-modal-dark"
      title={
        <Space>
          <ShareAltOutlined style={{ color: 'var(--amber, #ffb000)' }} />
          <span>{modalTitle || '分享'}</span>
        </Space>
      }
      open={visible}
      onCancel={onCancel}
      width={600}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          关闭
        </Button>
      ]}
    >
      <style>{DARK_MODAL_CSS}</style>
      <div style={{ padding: '16px 0' }}>
        {simple ? (
          /* 极简模式：三动作收敛——复制文案 / 复制链接 /（生成分享图待接终端出图链路，先隐藏） */
          <div>
            <Title level={5} style={{ marginTop: 0 }}>分享文案</Title>
            <TextArea
              rows={4}
              value={customMessage || generateShareContent('wechat')}
              onChange={(e) => setCustomMessage(e.target.value)}
              style={{ marginBottom: 10 }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <Button type="primary" icon={<CopyOutlined />} onClick={copyContent} style={{ flex: 1 }}>
                复制文案
              </Button>
              {hasUrl && (
                <Button icon={<LinkOutlined />} onClick={copyLink} style={{ flex: 1 }}>
                  复制链接
                </Button>
              )}
            </div>
            {hasUrl && (
              <div style={{ marginTop: 10, fontSize: 12, color: 'var(--mute2, #9aa6b2)', wordBreak: 'break-all' }}>
                {shareUrl}
                <a href={shareUrl} target="_blank" rel="noreferrer" style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>
                  打开 ↗
                </a>
              </div>
            )}
          </div>
        ) : (
        <>
        {/* 内容预览 */}
        <Card size="small" style={{ marginBottom: '24px', background: 'var(--surface-muted)' }}>
          <Title level={5} style={{ margin: '0 0 8px 0' }}>{target.title}</Title>
          <Paragraph ellipsis={{ rows: 3 }} style={{ margin: '0 0 8px 0', color: 'var(--text-muted)', whiteSpace: 'pre-wrap' }}>
            {target.summary}
          </Paragraph>
          {target.byline && (
            <Text type="secondary" style={{ fontSize: '12px' }}>
              {target.byline}
            </Text>
          )}
        </Card>

        <Divider />

        {/* 公开只读页：无链接时提供「一键生成」，生成后即翻转为下方链接区 */}
        {!hasUrl && (
          <>
            <div style={{ marginBottom: '24px' }}>
              <Title level={5}>公开链接</Title>
              <Button
                type="primary"
                icon={<LinkOutlined />}
                loading={generatingLink}
                onClick={handleGenerateLink}
                block
              >
                生成公开只读页（可搜索 / 可转发）
              </Button>
              <Text type="secondary" style={{ fontSize: '12px', display: 'block', marginTop: 8 }}>
                生成一个免登录、可被搜索引擎收录、社交可预览的只读结论页。
              </Text>
            </div>
            <Divider />
          </>
        )}

        {/* 分享链接（有可访问链接时显示） */}
        {hasUrl && (
          <>
            <div style={{ marginBottom: '24px' }}>
              <Title level={5}>分享链接</Title>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  value={shareUrl}
                  onChange={(e) => setShareUrl(e.target.value)}
                  placeholder="分享链接"
                  prefix={<LinkOutlined style={{ color: 'var(--text-muted)' }} />}
                />
                <Button icon={<CopyOutlined />} onClick={copyLink}>
                  复制
                </Button>
              </Space.Compact>
            </div>
            <Divider />
          </>
        )}

        {/* 分享平台 */}
        <div style={{ marginBottom: '24px' }}>
          <Title level={5}>分享到</Title>
          <Row gutter={[16, 16]}>
            {sharePlatforms.map(platform => (
              <Col xs={12} sm={8} md={6} key={platform.key}>
                <Button
                  type="default"
                  size="large"
                  onClick={platform.action}
                  style={{
                    width: '100%',
                    height: '60px',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderColor: platform.color,
                    color: platform.color
                  }}
                >
                  <div style={{ fontSize: '20px', marginBottom: '4px' }}>
                    {platform.icon}
                  </div>
                  <div style={{ fontSize: '12px' }}>{platform.name}</div>
                </Button>
              </Col>
            ))}
          </Row>
        </div>

        <Divider />

        {/* 自定义分享内容 */}
        <div>
          <Title level={5}>分享文案</Title>
          <TextArea
            rows={4}
            placeholder={generateShareContent('default')}
            value={customMessage}
            onChange={(e) => setCustomMessage(e.target.value)}
            style={{ marginBottom: '12px' }}
          />
          <Button
            type="primary"
            icon={<CopyOutlined />}
            onClick={copyContent}
            block
          >
            复制分享文案
          </Button>
        </div>

        <Divider />

        {/* 分享说明 */}
        <div style={{ background: 'rgba(16,185,129,0.08)', padding: '12px', borderRadius: '6px' }}>
          <Text type="secondary" style={{ fontSize: '12px' }}>
            {hasUrl ? (
              <>
                • 分享链接包含完整内容<br/>
                • 部分平台需要登录后才能分享<br/>
                • 分享内容会自动包含标题和摘要<br/>
                • 自定义文案会覆盖默认分享文本
              </>
            ) : (
              <>
                • 微信 / QQ 等无 Web 入口的平台会复制文案，直接粘贴即可<br/>
                • 微博会带文案跳转分享页<br/>
                • 文案默认包含结论摘要与来源，可自行编辑<br/>
                • 留空自定义文案时使用上方默认文本
              </>
            )}
          </Text>
        </div>
        </>
        )}
      </div>
    </Modal>
  );
};

export default ShareModal;
