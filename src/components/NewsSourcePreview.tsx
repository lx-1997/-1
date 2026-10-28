import React, { useMemo } from 'react';
import { parseArticleOriginal } from '../utils/articleOriginal';

/** Source-only material while the separate, validated AI analysis is pending. */
export default function NewsSourcePreview({ title, content }: { title: string; content: string }) {
  const source = useMemo(() => parseArticleOriginal({ title, content }), [title, content]);
  const points = source.takeaways;
  const paragraphs = source.sections.flatMap(section => section.paragraphs);
  if (!points.length && !paragraphs.length) return null;
  return <section className="bbt-news-source-preview" aria-label="等待期间的原文资料">
    <h3>{points.length ? '先看原文要点' : '先看原文摘录'}</h3>
    <p className="bbt-news-source-preview__note">以下是来源资料，AI 解读仍在生成。</p>
    {points.length ? <ul>{points.map((point, index) => <li key={index}>{point.text}</li>)}</ul>
      : paragraphs.slice(0, 2).map((paragraph, index) => <p key={index}>{paragraph.text}</p>)}
    <details><summary>展开已收录正文</summary><div>{content}</div></details>
  </section>;
}
