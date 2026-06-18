import React, { useEffect, useState } from 'react';
import { Modal, Button, Space, Typography, Input, message, Divider, Card, Row, Col } from 'antd';
import {
  ShareAltOutlined,
  WechatOutlined,
  WeiboOutlined,
  QqOutlined,
  TwitterOutlined,
  LinkOutlined,
  CopyOutlined,
  FacebookOutlined,
  LinkedinOutlined,
  MailOutlined
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
}

function postToTarget(post: LegacyPost): ShareTarget {
  return {
    title: post.title,
    summary: post.summary,
    byline: `作者：${post.author.username}`,
    url: `https://deepfocus.com/post/${post.id}`,
  };
}

const ShareModal: React.FC<ShareModalProps> = ({
  visible,
  onCancel,
  post,
  content,
  modalTitle,
}) => {
  const target: ShareTarget = content ?? (post ? postToTarget(post) : { title: '', summary: '' });

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

  // 生成分享内容
  const generateShareContent = (platform: string) => {
    const byline = target.byline ? `${target.byline}\n\n` : '';
    const baseContent = `${target.title}\n\n${target.summary}\n\n${byline}`;
    const link = hasUrl ? shareUrl : '';

    switch (platform) {
      case 'wechat':
        return `${baseContent}来自 DeepFocus 投研工作台${link ? `\n${link}` : ''}`;
      case 'weibo':
        return `${baseContent}#DeepFocus# #投研#${link ? ` ${link}` : ''}`;
      case 'qq':
        return `${baseContent}分享自 DeepFocus${link ? `：${link}` : ''}`;
      case 'twitter':
        return `${baseContent}From DeepFocus${link ? `\n${link}` : ''}`;
      case 'facebook':
      case 'linkedin':
        return `${baseContent}Shared from DeepFocus${link ? `\n${link}` : ''}`;
      case 'email':
        return `主题：${target.title}\n\n内容：${target.summary}\n\n${byline}${link ? `查看完整内容：${link}` : ''}`;
      default:
        return `${baseContent}${link}`.trim();
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

  // 分享到Twitter（文本即可，无需链接）
  const shareToTwitter = () => {
    const content = generateShareContent('twitter');
    const twitterUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(content)}`;
    window.open(twitterUrl, '_blank');
    message.success('正在跳转到 Twitter 分享页面');
  };

  // 分享到Facebook
  const shareToFacebook = () => {
    if (!hasUrl) {
      copyToClipboard(generateShareContent('facebook'), '已复制文案，可直接粘贴分享');
      return;
    }
    const content = generateShareContent('facebook');
    const facebookUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}&quote=${encodeURIComponent(content)}`;
    window.open(facebookUrl, '_blank');
    message.success('正在跳转到 Facebook 分享页面');
  };

  // 分享到LinkedIn
  const shareToLinkedIn = () => {
    if (!hasUrl) {
      copyToClipboard(generateShareContent('linkedin'), '已复制文案，可直接粘贴分享');
      return;
    }
    const linkedinUrl = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(target.title)}&summary=${encodeURIComponent(target.summary)}`;
    window.open(linkedinUrl, '_blank');
    message.success('正在跳转到 LinkedIn 分享页面');
  };

  // 分享到邮箱（mailto 携带文本即可）
  const shareToEmail = () => {
    const content = generateShareContent('email');
    const emailUrl = `mailto:?subject=${encodeURIComponent(target.title)}&body=${encodeURIComponent(content)}`;
    window.open(emailUrl);
    message.success('正在打开邮件客户端');
  };

  // 复制链接
  const copyLink = () => {
    copyToClipboard(shareUrl, '链接已复制到剪贴板');
  };

  // 复制完整内容
  const copyContent = () => {
    copyToClipboard(customMessage || generateShareContent('default'), '内容已复制到剪贴板');
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
    },
    {
      key: 'twitter',
      name: 'Twitter',
      icon: <TwitterOutlined style={{ color: '#1da1f2' }} />,
      color: '#1da1f2',
      action: shareToTwitter
    },
    {
      key: 'facebook',
      name: 'Facebook',
      icon: <FacebookOutlined style={{ color: '#1877f2' }} />,
      color: '#1877f2',
      action: shareToFacebook
    },
    {
      key: 'linkedin',
      name: 'LinkedIn',
      icon: <LinkedinOutlined style={{ color: '#0077b5' }} />,
      color: '#0077b5',
      action: shareToLinkedIn
    },
    {
      key: 'email',
      name: '邮件',
      icon: <MailOutlined style={{ color: 'var(--text-muted)' }} />,
      color: 'var(--text-muted)',
      action: shareToEmail
    }
  ];

  return (
    <Modal
      title={
        <Space>
          <ShareAltOutlined style={{ color: '#1890ff' }} />
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
      <div style={{ padding: '16px 0' }}>
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
                • 微博 / Twitter / 邮件会带文案跳转对应入口<br/>
                • 文案默认包含结论摘要与来源，可自行编辑<br/>
                • 留空自定义文案时使用上方默认文本
              </>
            )}
          </Text>
        </div>
      </div>
    </Modal>
  );
};

export default ShareModal;
