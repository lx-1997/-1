import React from 'react';
export interface StreamedResearchProse { intro?: string; summary?: string; sections?: Array<{ title?: string; paragraphs?: string[] }> }
export default function StreamedResearchBody({ prose, interrupted = false }: { prose: StreamedResearchProse; interrupted?: boolean }) {
  return <section className="bbt-streamed-body" aria-label="正在生成的研报正文">
    <small>{interrupted ? '生成已中断 · 以下为未完成正文，可手动重试' : '正文正在生成 · 尚未完成全文校验，结论可能调整'}</small>
    {typeof prose.intro === 'string' && <p className="bbt-streamed-body__intro">{prose.intro}</p>}
    {typeof prose.summary === 'string' && prose.summary !== prose.intro && <p>{prose.summary}</p>}
    {(Array.isArray(prose.sections) ? prose.sections : []).filter(section => section && typeof section === 'object').slice(0, 12).map((section, i) => <article key={i}>
      {typeof section.title === 'string' && <h3>{section.title}</h3>}
      {(Array.isArray(section.paragraphs) ? section.paragraphs : []).filter(p => typeof p === 'string').map((p, j) => <p key={j}>{p}</p>)}
    </article>)}
  </section>;
}
