import { useCallback, useEffect, useState } from 'react';
import { apiGet } from '../services/apiClient';
import './TerminalAiFundCard.css';

interface ArenaStrategy {
  fund_id: string;
  name: string;
  emoji: string;
  color: string;
  nav_pct: number;
  rank: number;
  win_rate: number | null;
  position_count: number;
  last_action: { side: string; name: string } | null;
  is_main: boolean;
}

interface ArenaResp {
  strategies: ArenaStrategy[];
  champion: string | null;
  spread: number | null;
  benchmark: { nav_pct: number | null };
  cycle: { stage: string; label: string; stance: string };
  in_session: boolean;
  phase_label: string;
}

// 与 premarket 雷达同一套客户端单飞 + TTL：多面板并发与重复轮询在网络层归一。
const ARENA_TTL_MS = 45_000;
let arenaCache: { at: number; data: ArenaResp } | null = null;
let arenaInFlight: Promise<ArenaResp> | null = null;

async function fetchArena(): Promise<ArenaResp> {
  if (arenaCache && Date.now() - arenaCache.at < ARENA_TTL_MS) {
    return arenaCache.data;
  }
  if (!arenaInFlight) {
    const request = apiGet<ArenaResp>('/api/ai-fund/arena').then(
      (data) => {
        arenaCache = { at: Date.now(), data };
        arenaInFlight = null;
        return data;
      },
      (error: unknown) => {
        arenaInFlight = null;
        throw error;
      },
    );
    arenaInFlight = request;
  }
  return arenaInFlight;
}

const pct = (v: number | null | undefined): string =>
  typeof v === 'number' ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—';

const ACTION_EMOJI: Record<string, string> = { buy: '买入', sell: '卖出' };

const TerminalAiFundCard: React.FC = () => {
  const [arena, setArena] = useState<ArenaResp | null>(null);

  const load = useCallback(async () => {
    try {
      setArena(await fetchArena());
    } catch {
      // 拉不到就整卡隐藏，不在终端首页报错打扰
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { void load(); }, 90_000);
    return () => window.clearInterval(timer);
  }, [load]);

  if (!arena || !arena.strategies?.length) {
    return null;
  }
  const ranked = [...arena.strategies].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const champ = ranked[0];
  const worst = ranked[ranked.length - 1];
  const cycle = arena.cycle || { stage: 'neutral', label: '温和轮动', stance: '' };

  return (
    <section className="bbt-aif" aria-label="AI 赛马实盘直播">
      <div className="bbt-aif-head">
        <div>
          <span className="bbt-aif-kicker">AI FUND ARENA</span>
          <h2>六位 AI 操盘手 · 实盘直播</h2>
          <p>{arena.in_session ? '盘中每 30 分钟一次决策，看谁在主线上吃到肉。' : `${arena.phase_label || '休市中'}：AI 仍在复盘与备弹，开盘自动开赛。`}</p>
        </div>
        <span className="bbt-aif-phase">{arena.in_session ? '● 撮合中' : (arena.phase_label || '休市')}</span>
      </div>
      <div className="bbt-aif-report">
        <span className="bbt-aif-champ" style={{ borderColor: champ?.color }}>
          🏆 {champ?.emoji} {champ?.name} <b className={champ && champ.nav_pct >= 0 ? 'up' : 'down'}>{pct(champ?.nav_pct)}</b>
        </span>
        <span className="bbt-aif-worst">垫底 {worst?.emoji} {worst?.name} <b className={worst && worst.nav_pct >= 0 ? 'up' : 'down'}>{pct(worst?.nav_pct)}</b></span>
        <span className="bbt-aif-bench">沪深300 <b className={(arena.benchmark?.nav_pct ?? 0) >= 0 ? 'up' : 'down'}>{pct(arena.benchmark?.nav_pct)}</b></span>
      </div>
      <div className="bbt-aif-cycle">
        <span className="bbt-aif-cycle-tag">🌊 {cycle.label}</span>
        {cycle.stance ? <span className="bbt-aif-cycle-note">{cycle.stance}</span> : null}
      </div>
      <div className="bbt-aif-rows">
        {ranked.slice(0, 3).map(s => (
          <div className="bbt-aif-row" key={s.fund_id}>
            <i className="bbt-aif-dot" style={{ background: s.color }} />
            <span className="bbt-aif-name">{s.emoji} {s.name}</span>
            <span className={`bbt-aif-nav ${s.nav_pct >= 0 ? 'up' : 'down'}`}>{pct(s.nav_pct)}</span>
            <span className="bbt-aif-act">
              {s.last_action
                ? `${s.last_action.side === 'buy' ? '买入' : '卖出'} ${s.last_action.name}`
                : (s.position_count > 0 ? `持有 ${s.position_count} 只` : '观望中')}
            </span>
          </div>
        ))}
      </div>
      <a className="bbt-aif-cta" href="/ai-fund">
        v2 规则开赛：情绪周期入引擎，六人各带 ¥100 万 · 看完整直播 →
      </a>
    </section>
  );
};

export default TerminalAiFundCard;
