import React, { useEffect, useMemo, useState } from 'react';
import {
  CheckCircleOutlined,
  ClusterOutlined,
  LinkOutlined,
  SafetyCertificateOutlined,
  TableOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import {
  ContentOntologyGraphNode,
  ContentOntologyItem,
  ContentOntologyMap,
} from '../services/ontologyService';
import './ContentSemanticMap.css';

type MapMode = 'graph' | 'matrix';

interface PositionedNode extends ContentOntologyGraphNode {
  x: number;
  y: number;
}

interface ContentSemanticMapProps {
  data: ContentOntologyMap;
}

const FACET_ORDER = ['content_type', 'entity', 'event', 'theme', 'signal', 'horizon', 'source'];

const CONTENT_TYPE_COLORS: Record<string, string> = {
  flash: '#22d3ee',
  article: '#60a5fa',
  research: '#a78bfa',
  institution_note: '#f59e0b',
  evidence: '#94a3b8',
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || '时间未知';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function layoutGraphNodes(nodes: ContentOntologyGraphNode[], compact = false): PositionedNode[] {
  const asset = nodes.find(node => node.kind === 'asset');
  const contents = nodes.filter(node => node.kind === 'content').slice(0, 8);
  const tags = nodes.filter(node => node.kind === 'tag').slice(0, 12);
  const positioned: PositionedNode[] = [];
  if (asset) positioned.push({ ...asset, x: 51, y: 50 });

  const contentColumns = compact ? 1 : contents.length > 6 ? 2 : 1;
  const rows = Math.ceil(contents.length / contentColumns);
  contents.forEach((node, index) => {
    const column = contentColumns === 1 ? 0 : index % 2;
    const row = contentColumns === 1 ? index : Math.floor(index / 2);
    positioned.push({
      ...node,
      x: compact ? 15 : contentColumns === 1 ? 13 : 8 + column * 18,
      y: rows <= 1 ? 50 : (compact ? 7 : 12) + (row / (rows - 1)) * (compact ? 86 : 76),
    });
  });

  const tagsByFacet = new Map<string, ContentOntologyGraphNode[]>();
  tags.forEach(node => {
    const group = tagsByFacet.get(node.facet) || [];
    group.push(node);
    tagsByFacet.set(node.facet, group);
  });
  const orderedTags = FACET_ORDER.flatMap(facet => tagsByFacet.get(facet) || []);
  const tagColumns = compact ? 1 : orderedTags.length > 8 ? 2 : 1;
  const tagRows = Math.ceil(orderedTags.length / tagColumns);
  orderedTags.forEach((node, index) => {
    const column = tagColumns === 1 ? 0 : index % 2;
    const row = tagColumns === 1 ? index : Math.floor(index / 2);
    positioned.push({
      ...node,
      x: compact ? 86 : tagColumns === 1 ? 86 : 76 + column * 16,
      y: tagRows <= 1 ? 50 : (compact ? 6 : 11) + (row / (tagRows - 1)) * (compact ? 88 : 78),
    });
  });
  return positioned;
}

function tagsByFacet(item: ContentOntologyItem): Map<string, ContentOntologyItem['tags']> {
  const groups = new Map<string, ContentOntologyItem['tags']>();
  item.tags.forEach(tag => {
    const entries = groups.get(tag.facet) || [];
    entries.push(tag);
    groups.set(tag.facet, entries);
  });
  return groups;
}

const ContentSemanticMap: React.FC<ContentSemanticMapProps> = ({ data }) => {
  const [mode, setMode] = useState<MapMode>('graph');
  const [activeFacet, setActiveFacet] = useState('all');
  const [activeTagId, setActiveTagId] = useState<string>();
  const [selectedContentId, setSelectedContentId] = useState<string>();
  const [compactGraph, setCompactGraph] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches,
  );

  useEffect(() => {
    const media = window.matchMedia('(max-width: 700px)');
    const updateGraphDensity = () => setCompactGraph(media.matches);
    media.addEventListener('change', updateGraphDensity);
    return () => media.removeEventListener('change', updateGraphDensity);
  }, []);

  const selectedItem = data.items.find(item => item.id === selectedContentId) || data.items[0];
  const positionedNodes = useMemo(
    () => layoutGraphNodes(data.graph.nodes, compactGraph),
    [compactGraph, data.graph.nodes],
  );
  const nodeMap = useMemo(
    () => new Map(positionedNodes.map(node => [node.id, node])),
    [positionedNodes],
  );
  const connectedContentIds = useMemo(() => {
    if (!activeTagId) return null;
    return new Set(
      data.graph.edges
        .filter(edge => edge.target === activeTagId)
        .map(edge => edge.source),
    );
  }, [activeTagId, data.graph.edges]);
  const selectedGroups = useMemo(
    () => selectedItem ? tagsByFacet(selectedItem) : new Map<string, ContentOntologyItem['tags']>(),
    [selectedItem],
  );
  const toggleTag = (tagId: string) => {
    setActiveTagId(current => current === tagId ? undefined : tagId);
  };

  return (
    <div className="semantic-map-workspace">
      <section className="semantic-map-hero">
        <div>
          <span>CONTENT GRAPH · 内容关系图</span>
          <h2>信息不再散落，一眼看清它们为何相关</h2>
          <p>
            快讯、文章、研报和机构纪要已经连接到同一标的。点击节点，即可查看关系和来源。
          </p>
          <div className="semantic-map-summary" aria-label="内容本体摘要">
            <span><strong>{data.stats.content_count}</strong> 条内容</span>
            <span><strong>{data.stats.tag_count}</strong> 个标签关系</span>
            <span><strong>{data.stats.avg_facets_per_content}</strong> 个平均维度</span>
          </div>
        </div>
        <div className="semantic-map-health">
          <i style={{ '--coverage': `${data.stats.ontology_coverage}%` } as React.CSSProperties}>
            <strong>{data.stats.ontology_coverage}%</strong>
          </i>
          <span>
            <strong>本体覆盖率</strong>
            <small>具备至少 5 个语义维度</small>
          </span>
        </div>
      </section>

      <section className="semantic-facet-ribbon">
        <button
          type="button"
          className={activeFacet === 'all' ? 'active' : ''}
          onClick={() => {
            setActiveFacet('all');
            setActiveTagId(undefined);
          }}
        >
          全部
        </button>
        {data.facets
          .filter(facet => ['event', 'theme', 'signal'].includes(facet.facet))
          .map(facet => (
          <button
            key={facet.facet}
            type="button"
            className={activeFacet === facet.facet ? 'active' : ''}
            style={{ '--facet-color': facet.color } as React.CSSProperties}
            onClick={() => {
              setActiveFacet(facet.facet);
              setActiveTagId(undefined);
            }}
          >
            <i />
            {facet.label}
          </button>
        ))}
      </section>

      <div className="semantic-main-layout">
        <section className="semantic-visual-panel">
          <header>
            <div>
              <span>SEMANTIC PROJECTION</span>
              <h3>{mode === 'graph' ? '交互语义地图' : '内容 × 语义维度矩阵'}</h3>
            </div>
            <div className="semantic-mode-switcher" role="group" aria-label="切换语义图展示">
              <button
                type="button"
                className={mode === 'graph' ? 'active' : ''}
                onClick={() => setMode('graph')}
              >
                <ClusterOutlined /> 关系图
              </button>
              <button
                type="button"
                className={mode === 'matrix' ? 'active' : ''}
                onClick={() => setMode('matrix')}
              >
                <TableOutlined /> 标签矩阵
              </button>
            </div>
          </header>

          {mode === 'graph' ? (
            <div className={`semantic-graph-canvas facet-${activeFacet}`}>
              <div className="semantic-graph-lanes" aria-hidden>
                <span>内容对象</span>
                <span>核心证券</span>
                <span>语义标签</span>
              </div>
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="内容本体语义关系图">
                {data.graph.edges.map(edge => {
                  const source = nodeMap.get(edge.source);
                  const target = nodeMap.get(edge.target);
                  if (!source || !target) return null;
                  const related = !activeTagId
                    || edge.target === activeTagId
                    || connectedContentIds?.has(edge.source);
                  const midX = (source.x + target.x) / 2;
                  return (
                    <g
                      key={edge.id}
                      className={`semantic-edge ${edge.type.toLowerCase()}${related ? '' : ' muted'}`}
                    >
                      <path
                        d={`M ${source.x} ${source.y} C ${midX} ${source.y}, ${midX} ${target.y}, ${target.x} ${target.y}`}
                      />
                    </g>
                  );
                })}
              </svg>
              {positionedNodes.map(node => {
                const item = node.kind === 'content'
                  ? data.items.find(candidate => candidate.id === node.id)
                  : undefined;
                const tagActive = node.kind === 'tag' && node.id === activeTagId;
                const facetMuted = activeFacet !== 'all'
                  && node.kind === 'tag'
                  && node.facet !== activeFacet;
                const relationMuted = connectedContentIds
                  && node.kind === 'content'
                  && !connectedContentIds.has(node.id);
                return (
                  <button
                    key={node.id}
                    type="button"
                    className={[
                      'semantic-graph-node',
                      node.kind,
                      tagActive ? 'active' : '',
                      item?.id === selectedItem?.id ? 'selected' : '',
                      facetMuted || relationMuted ? 'muted' : '',
                    ].filter(Boolean).join(' ')}
                    style={{
                      left: `${node.x}%`,
                      top: `${node.y}%`,
                      '--node-color': node.kind === 'content'
                        ? CONTENT_TYPE_COLORS[node.content_type || 'evidence']
                        : node.color,
                    } as React.CSSProperties}
                    onClick={() => {
                      if (node.kind === 'content') setSelectedContentId(node.id);
                      if (node.kind === 'tag') toggleTag(node.id);
                    }}
                  >
                    <i />
                    <small>{node.subtitle}</small>
                    <strong>{node.label}</strong>
                    <em>{node.kind === 'tag' ? `${node.weight} 条内容` : node.kind === 'content' ? `${node.weight} 个标签` : 'Canonical ID'}</em>
                  </button>
                );
              })}
              <footer>
                <span><i className="content" />内容</span>
                <span><i className="asset" />证券对象</span>
                <span><i className="tag" />类型化标签</span>
                <em>{activeTagId ? '已聚焦标签，点击空白标签重置' : '点击任一对象查看关系'}</em>
              </footer>
            </div>
          ) : (
            <div className="semantic-matrix">
              <div className="semantic-matrix-head">
                <span>内容对象</span>
                {data.facets.map(facet => (
                  <strong key={facet.facet} style={{ '--facet-color': facet.color } as React.CSSProperties}>
                    {facet.label}
                  </strong>
                ))}
                <em>质量</em>
              </div>
              {data.items.slice(0, 16).map(item => {
                const groups = tagsByFacet(item);
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={item.id === selectedItem?.id ? 'selected' : ''}
                    onClick={() => setSelectedContentId(item.id)}
                  >
                    <span>
                      <i style={{ background: CONTENT_TYPE_COLORS[item.content_type] }} />
                      <strong>{item.title}</strong>
                      <small>{item.content_type_label} · {item.source_name || 'DAO财经'}</small>
                    </span>
                    {data.facets.map(facet => (
                      <span className="semantic-matrix-cell" key={facet.facet}>
                        {(groups.get(facet.facet) || []).slice(0, 2).map(tag => (
                          <em key={tag.id} style={{ '--tag-color': tag.color } as React.CSSProperties}>
                            {tag.label}
                          </em>
                        ))}
                      </span>
                    ))}
                    <output>{Math.round(item.annotation_quality * 100)}%</output>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <aside className="semantic-inspector">
          <header>
            <span>OBJECT INSPECTOR</span>
            <h3>内容对象详情</h3>
          </header>
          {selectedItem ? (
            <>
              <div className="semantic-inspector-type">
                <i style={{ background: CONTENT_TYPE_COLORS[selectedItem.content_type] }} />
                <strong>{selectedItem.content_type_label}</strong>
                <em>{Math.round(selectedItem.annotation_quality * 100)}% 标注质量</em>
              </div>
              <h4>{selectedItem.title}</h4>
              <p>{selectedItem.summary || '该内容暂无摘要，仍可通过标签和原始对象继续追溯。'}</p>
              <dl>
                <div><dt>来源</dt><dd>{selectedItem.source_name || 'DAO财经'}</dd></div>
                <div><dt>时间</dt><dd>{formatTime(selectedItem.published_at)}</dd></div>
                <div><dt>标签</dt><dd>{selectedItem.tag_count}</dd></div>
                <div><dt>语义维度</dt><dd>{selectedItem.facet_count}</dd></div>
              </dl>
              <div className="semantic-inspector-tags">
                {FACET_ORDER.map(facet => {
                  const tags = selectedGroups.get(facet) || [];
                  if (!tags.length) return null;
                  return (
                    <div key={facet}>
                      <span>{tags[0].facet_label}</span>
                      <p>
                        {tags.map(tag => (
                          <button
                            key={tag.id}
                            type="button"
                            style={{ '--tag-color': tag.color } as React.CSSProperties}
                            onClick={() => {
                              setMode('graph');
                              setActiveFacet(tag.facet);
                              setActiveTagId(tag.id);
                            }}
                          >
                            {tag.label}
                            <small>{Math.round(tag.confidence * 100)}%</small>
                          </button>
                        ))}
                      </p>
                    </div>
                  );
                })}
              </div>
              <div className="semantic-provenance">
                <SafetyCertificateOutlined />
                <span>
                  <strong>可追溯标注</strong>
                  <small>规则引擎与 Canonical ID 生成；置信度和来源已写入关系表。</small>
                </span>
              </div>
              {selectedItem.url && (
                <a href={selectedItem.url} target="_blank" rel="noreferrer">
                  查看原始内容 <LinkOutlined />
                </a>
              )}
            </>
          ) : (
            <div className="semantic-inspector-empty">
              <CheckCircleOutlined />
              <strong>暂无可检查内容</strong>
            </div>
          )}
        </aside>
      </div>

      <details className="semantic-advanced-panel">
        <summary>
          <span>
            <TagsOutlined />
            查看完整标签库
          </span>
          <em>{data.stats.unique_tag_count} 个语义对象</em>
        </summary>
        <section className="semantic-tag-cloud">
          <header>
            <div>
              <span>TAG LIBRARY</span>
              <h3>{activeFacet === 'all' ? '全部标签' : data.facets.find(facet => facet.facet === activeFacet)?.label}</h3>
            </div>
            <small>选择标签，可反查所有关联内容</small>
          </header>
          <div>
            {data.facets
              .filter(facet => activeFacet === 'all' || facet.facet === activeFacet)
              .flatMap(facet => facet.items)
              .sort((a, b) => b.count - a.count)
              .slice(0, 28)
              .map(tag => (
                <button
                  key={tag.id}
                  type="button"
                  className={activeTagId === tag.id ? 'active' : ''}
                  style={{
                    '--tag-color': tag.color,
                    '--tag-scale': `${Math.min(1.2, 0.94 + tag.count * 0.02)}`,
                  } as React.CSSProperties}
                  onClick={() => {
                    setMode('graph');
                    setActiveFacet(tag.facet);
                    toggleTag(tag.id);
                  }}
                >
                  <i />
                  <span>{tag.label}</span>
                  <em>{tag.count}</em>
                </button>
              ))}
          </div>
        </section>
      </details>
    </div>
  );
};

export default ContentSemanticMap;
