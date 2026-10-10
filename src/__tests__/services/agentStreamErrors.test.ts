import { friendlyStreamError } from '../../services/agentService';

it.each(['NetworkError', 'Failed to fetch', 'Load failed'])('shows a readable message for browser error %s', message => {
  expect(friendlyStreamError(message)).toBe('网络连接不稳定，请稍后重试；多次失败请重新登录后再试');
});

it('preserves backend guidance and provides a default for empty errors', () => {
  expect(friendlyStreamError('登录已失效，请重新登录')).toBe('登录已失效，请重新登录');
  expect(friendlyStreamError()).toBe('AI 服务连接失败');
});
