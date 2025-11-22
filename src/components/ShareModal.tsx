import React, { useState } from 'react';
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

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface ShareModalProps {
  visible: boolean;
  onCancel: () => void;
  post: {
    id: string;
    title: string;
    summary: string;
    author: {
      username: string;
    };
  };
}

const ShareModal: React.FC<ShareModalProps> = ({
  visible,
  onCancel,
  post
}) => {
  const [customMessage, setCustomMessage] = useState('');
  const [shareUrl, setShareUrl] = useState(`https://deepfocus.com/post/${post.id}`);

  // 生成分享内容
  const generateShareContent = (platform: string) => {
    const baseContent = `${post.title}\n\n${post.summary}\n\n作者：${post.author.username}\n\n`;
    const url = shareUrl;
    
    switch (platform) {
      case 'wechat':
        return `${baseContent}来自深度焦点个股投研智库\n${url}`;
      case 'weibo':
        return `${baseContent}#深度焦点# #股票分析# ${url}`;
      case 'qq':
        return `${baseContent}分享自深度焦点：${url}`;
      case 'twitter':
        return `${baseContent}From DeepFocus Stock Research Platform\n${url}`;
      case 'facebook':
        return `${baseContent}Shared from DeepFocus Stock Research Platform\n${url}`;
      case 'linkedin':
        return `${baseContent}Shared from DeepFocus Stock Research Platform\n${url}`;
      case 'email':
        return `主题：${post.title}\n\n内容：${post.summary}\n\n作者：${post.author.username}\n\n查看完整内容：${url}`;
      default:
        return `${baseContent}${url}`;
    }
  };

  // 分享到微信
  const shareToWechat = () => {
    try {
      const content = generateShareContent('wechat');
      // 模拟分享到微信
      navigator.clipboard.writeText(content).then(() => {
        message.success('已复制到剪贴板，可以粘贴到微信分享');
      }).catch(() => {
        message.error('复制失败，请手动复制');
      });
    } catch (error) {
      console.error('分享到微信失败:', error);
      message.error('分享失败');
    }
  };

  // 分享到微博
  const shareToWeibo = () => {
    const content = generateShareContent('weibo');
    const weiboUrl = `https://service.weibo.com/share/share.php?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(content)}`;
    window.open(weiboUrl, '_blank');
    message.success('正在跳转到微博分享页面');
  };

  // 分享到QQ
  const shareToQQ = () => {
    const content = generateShareContent('qq');
    const qqUrl = `https://connect.qq.com/widget/shareqq/index.html?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(post.title)}&summary=${encodeURIComponent(post.summary)}`;
    window.open(qqUrl, '_blank');
    message.success('正在跳转到QQ分享页面');
  };

  // 分享到Twitter
  const shareToTwitter = () => {
    const content = generateShareContent('twitter');
    const twitterUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(content)}`;
    window.open(twitterUrl, '_blank');
    message.success('正在跳转到Twitter分享页面');
  };

  // 分享到Facebook
  const shareToFacebook = () => {
    const content = generateShareContent('facebook');
    const facebookUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}&quote=${encodeURIComponent(content)}`;
    window.open(facebookUrl, '_blank');
    message.success('正在跳转到Facebook分享页面');
  };

  // 分享到LinkedIn
  const shareToLinkedIn = () => {
    const content = generateShareContent('linkedin');
    const linkedinUrl = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(shareUrl)}&title=${encodeURIComponent(post.title)}&summary=${encodeURIComponent(post.summary)}`;
    window.open(linkedinUrl, '_blank');
    message.success('正在跳转到LinkedIn分享页面');
  };

  // 分享到邮箱
  const shareToEmail = () => {
    const content = generateShareContent('email');
    const emailUrl = `mailto:?subject=${encodeURIComponent(post.title)}&body=${encodeURIComponent(content)}`;
    window.open(emailUrl);
    message.success('正在打开邮件客户端');
  };

  // 复制链接
  const copyLink = () => {
    try {
      navigator.clipboard.writeText(shareUrl).then(() => {
        message.success('链接已复制到剪贴板');
      }).catch(() => {
        message.error('复制失败，请手动复制');
      });
    } catch (error) {
      console.error('复制链接失败:', error);
      message.error('复制失败');
    }
  };

  // 复制完整内容
  const copyContent = () => {
    try {
      const content = customMessage || generateShareContent('default');
      navigator.clipboard.writeText(content).then(() => {
        message.success('内容已复制到剪贴板');
      }).catch(() => {
        message.error('复制失败，请手动复制');
      });
    } catch (error) {
      console.error('复制内容失败:', error);
      message.error('复制失败');
    }
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
      icon: <MailOutlined style={{ color: '#666' }} />,
      color: '#666',
      action: shareToEmail
    }
  ];

  return (
    <Modal
      title={
        <Space>
          <ShareAltOutlined style={{ color: '#1890ff' }} />
          <span>分享帖子</span>
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
        {/* 帖子信息预览 */}
        <Card size="small" style={{ marginBottom: '24px', background: '#f8f9fa' }}>
          <Title level={5} style={{ margin: '0 0 8px 0' }}>{post.title}</Title>
          <Paragraph ellipsis={{ rows: 2 }} style={{ margin: '0 0 8px 0', color: '#666' }}>
            {post.summary}
          </Paragraph>
          <Text type="secondary" style={{ fontSize: '12px' }}>
            作者：{post.author.username}
          </Text>
        </Card>

        <Divider />

        {/* 分享链接 */}
        <div style={{ marginBottom: '24px' }}>
          <Title level={5}>分享链接</Title>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              value={shareUrl}
              onChange={(e) => setShareUrl(e.target.value)}
              placeholder="分享链接"
            />
            <Button icon={<CopyOutlined />} onClick={copyLink}>
              复制
            </Button>
          </Space.Compact>
        </div>

        <Divider />

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
          <Title level={5}>自定义分享内容</Title>
          <TextArea
            rows={4}
            placeholder="自定义分享内容（可选）"
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
            复制自定义内容
          </Button>
        </div>

        <Divider />

        {/* 分享说明 */}
        <div style={{ background: '#f6ffed', padding: '12px', borderRadius: '6px' }}>
          <Text type="secondary" style={{ fontSize: '12px' }}>
            • 分享链接包含完整的帖子内容<br/>
            • 部分平台需要登录后才能分享<br/>
            • 分享内容会自动包含帖子标题和摘要<br/>
            • 自定义内容会覆盖默认分享文本
          </Text>
        </div>
      </div>
    </Modal>
  );
};

export default ShareModal;
