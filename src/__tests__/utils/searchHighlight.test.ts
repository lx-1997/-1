import { splitByKeywords } from '../../utils/searchHighlight';

const texts = (segs: ReturnType<typeof splitByKeywords>) => segs.map(s => s.text).join('');
const hits = (segs: ReturnType<typeof splitByKeywords>) => segs.filter(s => s.hit).map(s => s.text);

describe('splitByKeywords', () => {
  it('关键词为空时整段不标命中', () => {
    const segs = splitByKeywords('宏和科技：电子布涨价', '');
    expect(segs).toEqual([{ text: '宏和科技：电子布涨价', hit: false }]);
    expect(splitByKeywords('任意文本', '   ')).toEqual([{ text: '任意文本', hit: false }]);
  });

  it('空文本返回空数组', () => {
    expect(splitByKeywords('', '建滔')).toEqual([]);
  });

  it('中文关键词多次命中', () => {
    const segs = splitByKeywords('建滔集团发布涨价通知，建滔电子布产能领先', '建滔');
    expect(hits(segs)).toEqual(['建滔', '建滔']);
    expect(texts(segs)).toBe('建滔集团发布涨价通知，建滔电子布产能领先');
    expect(segs[0].hit).toBe(true);
  });

  it('拉丁字母忽略大小写', () => {
    const segs = splitByKeywords('CCL产业链顺畅，ccl涨价', 'CCL');
    expect(hits(segs)).toEqual(['CCL', 'ccl']);
  });

  it('多关键词空白分词，任一词命中即标出', () => {
    const segs = splitByKeywords('电子布涨价 覆铜板紧缺', '覆铜板 电子布');
    expect(hits(segs)).toEqual(['电子布', '覆铜板']);
    expect(texts(segs)).toBe('电子布涨价 覆铜板紧缺');
  });

  it('正则元字符按字面匹配，不抛异常', () => {
    const segs = splitByKeywords('成本占比a(b高', 'a(b');
    expect(hits(segs)).toEqual(['a(b']);
    expect(() => splitByKeywords('普通文本', '.*[](')).not.toThrow();
  });

  it('切分无损：任意关键词拼回原文', () => {
    const raw = '当前电子布到CCL涨价传导顺畅：10月13日，建滔集团再度发布涨价通知。';
    for (const kw of ['涨价', '建滔 涨价', 'xyz', 'CCL：', '日']) {
      expect(texts(splitByKeywords(raw, kw))).toBe(raw);
    }
  });
});
