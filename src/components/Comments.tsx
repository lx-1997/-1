import React, { useState } from 'react';
import { Card, List, Input, Button, Typography, Space, Avatar } from 'antd';
import { AppState } from '../types';

const { Title, Text } = Typography;

interface CommentsProps {
  appState: AppState;
  onAddComment?: (text: string) => void;
}

interface LocalComment {
  id: string;
  author: string;
  content: string;
  timestamp: number;
}

const Comments: React.FC<CommentsProps> = ({ appState, onAddComment }) => {
  const [comments, setComments] = useState<LocalComment[]>([]);
  const [text, setText] = useState('');

  const submit = () => {
    if (!text.trim()) return;
    const newComment: LocalComment = {
      id: `${Date.now()}`,
      author: appState.user?.username || '游客',
      content: text.trim(),
      timestamp: Date.now()
    };
    setComments(prev => [newComment, ...prev]);
    setText('');
    onAddComment?.(newComment.content);
  };

  return (
    <div style={{ padding: '24px' }}>
      <Title level={2} style={{ marginBottom: '24px' }}>
        评论区
      </Title>

      <Card style={{ marginBottom: '16px' }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea
            rows={3}
            placeholder="发表你的看法..."
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div style={{ textAlign: 'right' }}>
            <Button type="primary" onClick={submit}>发布</Button>
          </div>
        </Space>
      </Card>

      <Card>
        <List
          dataSource={comments}
          locale={{ emptyText: '还没有评论，来说两句吧～' }}
          renderItem={(item) => (
            <List.Item key={item.id}>
              <List.Item.Meta
                avatar={<Avatar>{item.author[0].toUpperCase()}</Avatar>}
                title={
                  <Space>
                    <Text strong>{item.author}</Text>
                    <Text type="secondary" style={{ fontSize: '12px' }}>
                      {new Date(item.timestamp).toLocaleString('zh-CN')}
                    </Text>
                  </Space>
                }
                description={<div style={{ whiteSpace: 'pre-wrap' }}>{item.content}</div>}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
};

export default Comments;


