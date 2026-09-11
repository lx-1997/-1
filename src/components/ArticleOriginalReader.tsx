import React, { useMemo } from 'react';
import { parseArticleOriginal, type ArticleOriginalParagraph } from '../utils/articleOriginal';
import './ArticleOriginalReader.css';

interface ArticleOriginalReaderProps {
  title: string;
  sourceName?: string | null;
  createdAt?: string;
  paragraphs?: string[];
  content?: string;
  loading?: boolean;
  error?: string;
  parser?: string;
  truncated?: boolean;
  sourceImageUrl?: string;
  onRetry?: () => void;
}

function formatDate(value?: string): string {
  if (!value) return '';
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(value)).replace(/\//g, '-');
  } catch { return ''; }
}

function OriginalToggle({ paragraph }: { paragraph: ArticleOriginalParagraph }) {
  if (!paragraph.original) return null;
  return (
    <details className="bbt-article-original__source">
      <summary>查看英文原文 · English original</summary>
      <p>{paragraph.original}</p>
    </details>
  );
}

const ArticleOriginalReader: React.FC<ArticleOriginalReaderProps> = ({
  title, sourceName, createdAt, paragraphs, content, loading, error, parser, truncated, sourceImageUrl, onRetry,
}) => {
  const source = useMemo(() => parseArticleOriginal({ paragraphs, content, title }), [content, paragraphs, title]);
  const hasBody = source.takeaways.length > 0 || source.sections.some(section => section.paragraphs.length > 0);
  const isPdf = /\.(pdf)(\?|#|$)/i.test(sourceImageUrl || '');

  if (loading && !hasBody && !sourceImageUrl) {
    return (
      <div className="bbt-article-reader bbt-article-reader--loading" aria-live="polite">
        <div className="bbt-article-reader__loading-mark">原文阅读器</div>
        <h2>正在整理文章正文</h2>
        <p>正在提取原文，并过滤网页菜单、相关推荐和版权尾巴…</p>
        <div className="bbt-article-reader__skeleton" />
        <div className="bbt-article-reader__skeleton bbt-article-reader__skeleton--short" />
      </div>
    );
  }

  return (
    <article className="bbt-article-reader">
      <div className="bbt-article-reader__inner">
        <header className="bbt-article-reader__header">
          <div className="bbt-article-reader__eyebrow">
            <span>深度文章 · 原文</span>
            {source.kicker && <span>{source.kicker}</span>}
          </div>
          <h1>{title}</h1>
          {source.englishTitle && <p className="bbt-article-reader__english-title">{source.englishTitle}</p>}
          <div className="bbt-article-reader__meta">
            {sourceName && <span>{sourceName}</span>}
            {source.byline && <span>{source.byline}</span>}
            {source.publishedAt && <span>{source.publishedAt}</span>}
            {!source.publishedAt && createdAt && <span>{formatDate(createdAt)}</span>}
          </div>
        </header>

        {error && (
          <div className="bbt-article-reader__alert" role="status">
            <span>{error}</span>
            {onRetry && <button type="button" onClick={onRetry}>重试提取</button>}
          </div>
        )}

        {sourceImageUrl && (
          <figure className="bbt-article-reader__source-file" aria-label="文章原始文件">
            <figcaption>
              原文原件 · 来源截图{isPdf ? '（PDF）' : ''}
            </figcaption>
            {isPdf ? (
              <object className="bbt-article-reader__pdf" data={sourceImageUrl} type="application/pdf" aria-label="文章原始 PDF">
                <a href={sourceImageUrl} target="_blank" rel="noopener noreferrer">在新窗口打开原文 PDF ↗</a>
              </object>
            ) : (
              <img className="bbt-article-reader__source-img" src={sourceImageUrl} alt={`${title} 原文原件`} loading="lazy" />
            )}
          </figure>
        )}

        {source.takeaways.length > 0 && (
          <section className="bbt-article-reader__takeaways" aria-label="文章要点">
            <div className="bbt-article-reader__section-label"><span>AI TAKEAWAYS</span><em>先看这 {source.takeaways.length} 个要点</em></div>
            <ol>
              {source.takeaways.map((paragraph, index) => (
                <li key={`takeaway-${index}`}>
                  <p>{paragraph.text}</p>
                  <OriginalToggle paragraph={paragraph} />
                </li>
              ))}
            </ol>
          </section>
        )}

        <div className="bbt-article-reader__body">
          {source.sections.map((section, sectionIndex) => (
            <section key={`section-${sectionIndex}`} className="bbt-article-reader__section">
              {section.heading && <h2>{section.heading}</h2>}
              {section.paragraphs.map((paragraph, index) => (
                <div key={`paragraph-${sectionIndex}-${index}`} className={`bbt-article-reader__paragraph bbt-article-reader__paragraph--${paragraph.language}`}>
                  <p>{paragraph.text}</p>
                  <OriginalToggle paragraph={paragraph} />
                </div>
              ))}
            </section>
          ))}
        </div>

        {!hasBody && !loading && !sourceImageUrl && <div className="bbt-article-reader__empty">暂无可阅读正文</div>}
        {sourceImageUrl
          ? <div className="bbt-article-reader__alert bbt-article-reader__alert--muted">上方为上游原始文件（真原文）；下方文字为机器提取副本，仅供检索与无障碍阅读，可能存在识别误差。</div>
          : parser === 'stored-fallback'
            ? <div className="bbt-article-reader__alert bbt-article-reader__alert--muted bbt-article-reader__alert--with-action">
              <span>来源全文暂时无法读取，已先展示已收录正文；稍后可重试补齐全文。</span>
              {onRetry && <button type="button" onClick={onRetry}>重试读取</button>}
            </div>
            : truncated && <div className="bbt-article-reader__alert bbt-article-reader__alert--muted">原文较长，当前显示的是已提取的前段文字。</div>}
        <footer className="bbt-article-reader__footer">
          {sourceImageUrl
            ? '原件直出：不经 AI 转写、不裁剪；链接为一次性预览，仅本次阅读有效。'
            : '已自动隐藏网页导航、相关推荐和版权尾巴，仅保留文章正文。'}
        </footer>
      </div>
    </article>
  );
};

export default ArticleOriginalReader;
