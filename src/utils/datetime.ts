import dayjs from 'dayjs';

/** 把后端 generated_at（ISO 时间）格式化为 MM-DD HH:mm:ss；无效/缺失则原样返回。 */
export function formatGeneratedAt(value?: string): string {
  if (!value) return '';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('MM-DD HH:mm:ss') : value;
}
