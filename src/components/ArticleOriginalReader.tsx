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
              上游原件 · {isPdf ? 'PDF' : '截图'}按原样呈现
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

        <div className="bbt-article-reader__body">
          {sourceImageUrl && <div className="bbt-article-reader__copy-note">文字对照版 · 自动提取自原件，个别字句可能有误差</div>}
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
        {!sourceImageUrl && parser === 'stored-fallback'
          ? <div className="bbt-article-reader__alert bbt-article-reader__alert--muted bbt-article-reader__alert--with-action">
            <span>来源全文暂时无法读取，已先展示已收录正文；稍后可重试补齐全文。</span>
            {onRetry && <button type="button" onClick={onRetry}>重试读取</button>}
          </div>
          : !sourceImageUrl && truncated && <div className="bbt-article-reader__alert bbt-article-reader__alert--muted">原文较长，当前显示的是已提取的前段文字。</div>}
        <footer className="bbt-article-reader__footer">
          {sourceImageUrl
            ? '正文以上游原件为准，文字版便于快速浏览与引用。'
            : '已自动隐藏网页导航、相关推荐和版权尾巴，仅保留文章正文。'}
        </footer>
      </div>
    </article>
  );
};

export default ArticleOriginalReader;
