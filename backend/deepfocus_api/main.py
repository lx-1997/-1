from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from dotenv import load_dotenv

from .agent_engines import DEFAULT_ENGINE_KEY, list_engines
from .agent_runtime import (
    cancel_investment_task,
    create_investment_task,
    get_investment_task,
    init_task_db,
    is_worker_running,
    list_investment_tasks,
    retry_investment_task,
    start_agent_worker,
    stop_agent_worker,
    task_counts,
)
from .agent_events import agent_task_event_stream
from .auth import (
    AuthMiddleware,
    AuthUserOut,
    InviteOverview,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserExistsError,
    UserListResponse,
    account_stats,
    invite_stats,
    authenticate,
    auth_required,
    claim_trial,
    count_recent_ip_registrations,
    count_users,
    is_disposable_email,
    membership_of_username,
    reg_ip_daily_max,
    turnstile_enabled,
    turnstile_secret,
    turnstile_sitekey,
    turnstile_soft,
    membership_source_of,
    create_access_token,
    create_user,
    current_claims,
    get_invite_overview,
    get_user_out_by_id,
    init_auth,
    is_valid_email,
    is_valid_phone,
    grant_membership,
    set_membership,
    set_membership_expiry,
    list_users,
    optional_current_user,
    require_admin,
    require_current_user,
    rotate_session,
    self_register_enabled,
)
from .user_prefs import get_watchlist as get_user_watchlist, set_watchlist as set_user_watchlist
from . import support_store
from . import membership_codes
from . import payment_config
from . import ashare_review
from . import ai_fund
from . import engagement
from . import track_record
from .ai_supply_chain import fetch_ai_supply_chain_capacity_trends
from .data_sources import (
    capture_agent_web_pages,
    create_data_source,
    delete_data_source,
    delete_data_item,
    delete_data_source_module_ref,
    get_data_item,
    get_data_source,
    init_data_source_db,
    keyword_crawl_data_source,
    list_data_source_module_refs,
    corpus_stats,
    crawl_evidence_if_thin,
    list_data_items,
    list_data_sources,
    list_data_tags,
    query_tokens_2gram,
    save_data_source_module_ref,
    store_upload_item,
    sync_data_source,
    update_data_item,
)
from .dulus_runtime import (
    build_dulus_runtime_status,
    create_dulus_memory,
    init_dulus_runtime_db,
    inspect_authorized_webbridge,
    list_dulus_memories,
    list_dulus_tools,
    run_dulus_roundtable,
)
from .file_tools import extract_local_file, extract_upload_file
from .cn_earnings_skill import (
    detect_cn_earnings_request,
    diagnose_cn_earnings,
    enrich_cn_earnings_record_detail,
    format_cn_earnings_skill_response,
    scan_cn_earnings,
)
from .customs_hs_detail import fetch_customs_hs_detail_snapshot, search_customs_hs_detail_products
from .customs_trade import build_customs_trade_analysis_text, fetch_customs_trade_snapshot
from .earnings_calendar import fetch_earnings_calendar
from .agent_tools import AgentTool, list_tools as list_agent_tool_specs, register_tool
from .mcp_tools import discover_mcp_agent_tools
from .data_store import (
    history as data_history,
    hot_symbols as data_hot_symbols,
    init_data_store,
    latest as data_latest,
    record as record_datapoint,
    stats as data_stats,
)
from . import seo_pages
from .metrics_store import (
    init_db as init_metrics_db,
    incr as metrics_incr,
    incr_research as metrics_incr_research,
    incr_ai_ref as metrics_incr_ai_ref,
    incr_hourly as metrics_incr_hourly,
    incr_news_heat as metrics_incr_news_heat,
    summary as metrics_summary,
    get_ai_cache as metrics_get_ai_cache,
    get_ai_cache_many as metrics_get_ai_cache_many,
    set_ai_cache as metrics_set_ai_cache,
    prune_ai_cache as metrics_prune_ai_cache,
    get_daily as metrics_get_daily,
    log_activity as metrics_log_activity,
    recent_activity as metrics_recent_activity,
    activity_actor_summary as metrics_activity_actors,
    activity_stats as metrics_activity_stats,
    member_activity_windows as metrics_member_activity,
)
from . import referral
from .llm import CloudResearchLLM, extract_citable_sources, tool_agent_to_orchestrator_response
from .market_data import fetch_market_quotes, search_market_symbols
from .market_layers import build_market_data_layer_status
from .major_event_skill import (
    detect_major_event_request,
    format_major_event_skill_response,
    scan_major_events,
)
from .mcp_hub import (
    call_mcp_tool,
    create_mcp_server,
    delete_mcp_server,
    discover_mcp_server,
    init_mcp_db,
    list_mcp_capabilities,
    list_mcp_servers,
)
from .model_config import public_model_config, save_model_config, configure_data_source_egress
from .multi_market_decision import build_multi_market_decision
from .tear_sheet import (
    build_briefing,
    build_macro_review,
    build_portfolio_review,
    build_tear_sheet,
    build_watchlist_summary,
)
from .options_signal import fetch_options_signals
from .official_news import fetch_official_news
from .people_voices import (
    FIGURES_BY_ID,
    digest_cache_key,
    fetch_people_spotlight,
    fetch_person_voices,
)
from .premarket_opportunity import build_premarket_opportunity_radar
from .professional_research import (
    analyze_professional_report,
    get_professional_report,
    ingest_professional_report_from_item,
    ingest_professional_report_text,
    init_professional_research_db,
    list_professional_chunks,
    list_professional_metrics,
    list_professional_reports,
    query_professional_rag,
    run_professional_eval,
)
from .shared_utils import clamp
from .risk_management import (
    calculate_greeks,
    calculate_position_risk,
    close_position,
    create_position,
    delete_position,
    get_pnl_summary,
    get_position,
    get_risk_limits,
    get_risk_summary,
    init_risk_db,
    list_pnl_records,
    list_positions,
    PositionAlreadyClosedError,
    refresh_position_prices,
    update_position,
    update_risk_limit,
)
from .backtest_engine import (
    calculate_backtest_metrics,
    compute_equity_curve_from_trades,
    create_backtest,
    delete_backtest,
    get_backtest,
    init_backtest_db,
    list_backtests,
    update_backtest,
)
from .backtest_executor import run_backtest, list_backtest_results
from .agent_loop import run_agent_research_loop
from .market_dashboard import (
    fetch_market_dashboard,
    fetch_ashare_dashboard,
)
from .tushare_data import fetch_ashare_structured_data
from .cross_module_aggregator import (
    gather_all_for_stock,
    build_injection_block,
)
from .realtime_messages import (
    create_realtime_message,
    init_realtime_message_db,
    list_realtime_messages,
    publish_data_source_items,
    realtime_message_event_stream,
    register_post_message_hook,
)
from .dao_bridge import run_dao_bridge
from . import growth_analytics
from .recall_subscriptions import (
    create_recall_subscription,
    delete_recall_subscription,
    dispatch_recall,
    init_recall_subscription_db,
    list_deliveries,
    send_alert_email,
    probe_wechat_online,
    list_recall_subscriptions,
    recall_metrics,
    recent_deliveries,
    resolve_recall_click,
)
from .share_snapshots import (
    create_share_snapshot,
    get_share_snapshot,
    increment_share_views,
    init_share_snapshot_db,
    render_not_found_html,
    render_share_page_html,
)
from .report_url_ingest import extract_report_url
from .eastmoney_reports import eastmoney_report_pdf_url, query_eastmoney_reports
from .research_vision import analyze_pdf_auto, analyze_news

# 对外 AI 品牌名：不暴露底层模型（如 MiniMax）
_AI_BRAND = (os.getenv("DEEPFOCUS_AI_BRAND") or "DEEPFOCUS 智能解读").strip()

_METRICS_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
<meta name="robots" content="noindex,nofollow"/>
<title>DEEPFOCUS 金融终端 · 数据看板</title>
<style>
  :root{--bg:#0a0d12;--card:#11161f;--line:#1c2530;--amber:#ffb000;--mute:#7f8a96;--up:#2bd96a;--blue:#6ab0ff;--purple:#c4b5fd}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:#e6ebf2;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;-webkit-text-size-adjust:100%}
  .wrap{max-width:960px;margin:0 auto;padding:20px 16px 48px}
  h1{font-size:17px;font-weight:700;letter-spacing:.5px;margin:6px 0 2px}
  .sub{color:var(--mute);font-size:12px;margin-bottom:16px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
  /* 北极星 Hero KPI 条：第一屏的视觉重心 */
  .kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:11px;margin:16px 0 16px}
  .kpit{position:relative;overflow:hidden;background:linear-gradient(160deg,#151c28,#0f131b);border:1px solid #232c39;border-radius:14px;padding:15px 16px 13px}
  .kpit::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--amber);opacity:.85}
  .kpit-k{color:#9aa6b4;font-size:12px;margin-bottom:9px;font-weight:600}
  .kpit-v{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1;color:#eef2f7}
  .kpit-v.amber{color:var(--amber)} .kpit-v.up{color:var(--up)} .kpit-v.blue{color:var(--blue)} .kpit-v.purple{color:var(--purple)} .kpit-v.warn{color:var(--amber)} .kpit-v.bad{color:#ff5a52}
  .kpit-d{font-size:13px;font-weight:700}
  .kpit-s{color:#7f8a96;font-size:11.5px;margin-top:8px;line-height:1.5}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 16px 13px}
  .stat .k{color:var(--mute);font-size:12px;margin-bottom:7px}
  .stat .v{font-size:28px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
  .stat .v.amber{color:var(--amber)} .stat .v.up{color:var(--up)} .stat .v.blue{color:var(--blue)} .stat .v.purple{color:var(--purple)}
  .stat .sm{color:var(--mute);font-size:11px;margin-top:6px;line-height:1.5}
  .up{color:var(--up)} .dn{color:#ff5a52}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
  .panel h2{font-size:13px;font-weight:700;color:#c7d2de;margin:0 0 12px;letter-spacing:.5px}
  .legend{display:flex;gap:14px;margin-bottom:10px;font-size:11px;color:var(--mute)}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
  .gbars{display:flex;align-items:flex-end;gap:8px;height:150px}
  .gday{flex:1 1 0;display:flex;flex-direction:column;align-items:center;min-width:0}
  .gset{display:flex;align-items:flex-end;gap:2px;height:120px;width:100%;justify-content:center}
  .gb{width:7px;border-radius:2px 2px 0 0;min-height:2px;transition:height .4s ease}
  .gd{color:var(--mute);font-size:9px;margin-top:6px;white-space:nowrap;transform:rotate(-35deg);transform-origin:center}
  .hbars{display:flex;align-items:flex-end;gap:3px;height:110px}
  .hb{flex:1 1 0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
  .hb .hbar{width:80%;background:linear-gradient(180deg,#6ab0ff,#1c3a5c);border-radius:2px 2px 0 0;min-height:2px}
  .hb .hl{color:var(--mute);font-size:8px;margin-top:4px}
  .dev{display:flex;height:30px;border-radius:6px;overflow:hidden;border:1px solid var(--line);margin-top:6px}
  .dev .seg{display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#04121f;white-space:nowrap}
  .devlab{display:flex;justify-content:space-between;font-size:12px;color:var(--mute);margin-top:8px}
  .two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  @media(max-width:680px){.two{grid-template-columns:1fr}}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th{color:var(--mute);font-weight:600;text-align:left;font-size:11px;padding:6px 6px;border-bottom:1px solid var(--line)}
  td{padding:7px 6px;border-bottom:1px solid #141a23;color:#d7dee7}
  td.t{max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  td.c{text-align:right;font-variant-numeric:tabular-nums;color:var(--amber);font-weight:700;white-space:nowrap}
  .rk{color:var(--mute);width:22px}
  .err{background:#2a1414;border:1px solid #5c1d1d;color:#ffb0b0;padding:16px;border-radius:10px;font-size:14px;line-height:1.7}
  .refresh{float:right;background:rgba(255,176,0,.1);color:#e6b455;border:1px solid rgba(255,176,0,.3);border-radius:999px;padding:5px 14px;font-size:12px;cursor:pointer;font-family:inherit}
  .ft{color:var(--mute);font-size:11px;text-align:center;margin-top:24px}
  /* —— 重排：分组导航 / 待办告警条 / 分区标题 —— */
  .nav{position:sticky;top:0;z-index:30;background:rgba(10,13,18,.93);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);display:flex;gap:4px;flex-wrap:wrap;padding:9px 0;margin:2px 0;border-bottom:1px solid var(--line)}
  .nav a{color:var(--mute);font-size:12px;text-decoration:none;padding:5px 11px;border-radius:999px;border:1px solid transparent;white-space:nowrap}
  .nav a:hover{color:#e6ebf2;background:#161d28;border-color:var(--line)}
  .todo{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0 4px}
  .pill{display:flex;flex-direction:column;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 14px;text-decoration:none;color:#cfd8e2}
  .pill b{font-size:21px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1;color:#e6ebf2}
  .pill .pl{color:var(--mute);font-size:11.5px}
  .pill.warn{border-color:#7a5a12;background:#1c1607} .pill.warn b{color:var(--amber)}
  .pill.bad{border-color:#5c1d1d;background:#1b0f0f} .pill.bad b{color:#ff5a52}
  .pill.ok{border-color:#1f4a2e;background:#0d1712} .pill.ok b{color:var(--up)}
  .pill:hover{border-color:#33414f}
  .group{display:flex;align-items:baseline;gap:10px;margin:30px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--line)}
  .group h2{font-size:15px;font-weight:800;color:#fff;margin:0;letter-spacing:.5px}
  .group .gs{color:var(--mute);font-size:11.5px}
  .anchor{scroll-margin-top:58px}
  /* —— 次要分析区折叠（默认收起，点开才看，减少主屏噪音） —— */
  details.fold{margin:30px 0 16px}
  details.fold>summary{cursor:pointer;list-style:none}
  details.fold>summary.group{margin:0 0 6px}
  details.fold>summary::-webkit-details-marker{display:none}
  details.fold>summary h2::before{content:'▸ ';color:var(--amber);font-weight:400}
  details.fold[open]>summary h2::before{content:'▾ '}
</style></head>
<body><div class="wrap" id="app"><div class="sub">加载中…</div></div>
<script>
const $=s=>document.querySelector(s);
const token=new URLSearchParams(location.search).get('token')||'';
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function card(k,v,cls,sm){return '<div class="stat"><div class="k">'+k+'</div><div class="v '+cls+'">'+(v==null?0:v)+'</div>'+(sm?'<div class="sm">'+sm+'</div>':'')+'</div>';}
function rate(a,b){ if(!b) return '—'; return (a/b*100).toFixed(1)+'%'; }
function lb(rows){ return (rows||[]).slice(0,10).map((t,i)=>'<tr><td class="rk">'+(i+1)+'</td><td class="t">'+esc(t.title||t.ref||'-')+'</td><td class="c">'+t.count+'</td></tr>').join('')||'<tr><td colspan=3 class="sub" style="padding:12px">暂无数据</td></tr>'; }
function setPill(id,cls,val,label){ var el=document.getElementById(id); if(!el) return; el.className='pill '+(cls||''); el.innerHTML='<b>'+val+'</b><span class="pl">'+label+'</span>'; }
function grp(id,title,sub){ return '<div class="group anchor" id="'+id+'"><h2>'+title+'</h2><span class="gs">'+(sub||'')+'</span></div>'; }
// 北极星大指标卡（Hero KPI）
function kpi(k,v,delta,sm,cls){return '<div class="kpit"><div class="kpit-k">'+k+'</div><div class="kpit-v '+(cls||'')+'">'+(v==null?'—':v)+(delta?' <span class="kpit-d">'+delta+'</span>':'')+'</div><div class="kpit-s">'+(sm||'')+'</div></div>';}
// 异步填充某个 Hero KPI 卡（会员数据后到）
function kset(id,v,sm,cls){var el=document.getElementById(id); if(!el)return; var vv=el.querySelector('.kpit-v'); if(vv){vv.className='kpit-v '+(cls||''); vv.innerHTML=(v==null?'—':v);} var s=el.querySelector('.kpit-s'); if(s)s.innerHTML=sm||'';}
// 折叠分区（抽屉）：summary 即分区头，点开才看
function grpD(id,title,sub){ return '<details class="fold"><summary class="group anchor" id="'+id+'"><h2>'+title+'</h2><span class="gs">'+(sub||'')+'</span></summary>'; }
async function load(){
  if(!token){ $('#app').innerHTML='<div class="err">缺少令牌。请用带 ?token=... 的网址打开本页。</div>'; return; }
  let d;
  try{ const r=await fetch('/api/metrics/summary?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); d=await r.json(); }
  catch(e){ $('#app').innerHTML='<div class="err">读取失败('+e.message+')。令牌可能不对，或稍后再试。</div>'; return; }
  const ty=d.pageviews_today||0, yd=d.pageviews_yesterday||0;
  const diff = yd? Math.round((ty-yd)/yd*100):0;
  const dayArrow = yd? (diff>=0?'<span class="up">▲'+diff+'%</span>':'<span class="dn">▼'+Math.abs(diff)+'%</span>'):'';
  // 多指标趋势
  const tr=d.trend||[]; const mx=Math.max(1,...tr.map(x=>Math.max(x.pageview,x.ai,x.copy)));
  const H=v=>Math.round(v/mx*100);
  const gbars = tr.map(x=>'<div class="gday" title="'+x.day+'  访问'+x.pageview+' · AI'+x.ai+' · 复制'+x.copy+'"><div class="gset">'+
      '<div class="gb" style="height:'+H(x.pageview)+'%;background:'+'#ffb000"></div>'+
      '<div class="gb" style="height:'+H(x.ai)+'%;background:#2bd96a"></div>'+
      '<div class="gb" style="height:'+H(x.copy)+'%;background:#6ab0ff"></div>'+
    '</div><div class="gd">'+(x.day||'').slice(5)+'</div></div>').join('') || '<div class="sub">暂无趋势数据</div>';
  // 时段活跃
  const hr=d.hourly||[]; const hmax=Math.max(1,...hr.map(x=>x.count));
  const hbars=hr.map(x=>'<div class="hb" title="'+x.h+':00  '+x.count+' 次"><div class="hbar" style="height:'+Math.round(x.count/hmax*100)+'%"></div><div class="hl">'+x.h+'</div></div>').join('');
  // 设备占比
  const dv=d.device||{mobile:0,desktop:0,total:0}; const dt=dv.total||0;
  const mp=dt?Math.round(dv.mobile/dt*100):0; const pp=dt?100-mp:0;
  const devBar = dt? '<div class="dev"><div class="seg" style="width:'+mp+'%;background:#2bd96a">'+(mp>=12?mp+'%':'')+'</div><div class="seg" style="width:'+pp+'%;background:#6ab0ff">'+(pp>=12?pp+'%':'')+'</div></div><div class="devlab"><span style="color:#2bd96a">📱 移动 '+dv.mobile+'</span><span style="color:#6ab0ff">💻 桌面 '+dv.desktop+'</span></div>' : '<div class="sub">暂无设备数据</div>';
  // 账号体系
  const a=d.accounts;
  const by=(a&&a.by_role)||{};
  const acctRows = a&&a.recent&&a.recent.length ? a.recent.map(function(u){ return '<tr><td class="t">'+esc(u.username)+'</td><td>'+esc(u.role)+'</td><td>'+(u.has_phone?'<span class="up">✓</span>':'—')+'</td><td>'+(u.has_email?'<span class="up">✓</span>':'—')+'</td><td class="c" style="color:#7f8a96;font-weight:400">'+esc(u.created_at)+'</td></tr>'; }).join('') : '<tr><td colspan=5 class="sub" style="padding:12px">暂无注册账号</td></tr>';
  const acctHtml = a ? (
    '<div class="grid">'+
      card('注册账号',a.total,'amber','今日 +'+(a.new_today||0)+' · 近7日 +'+(a.new_7d||0))+
      card('今日新增',a.new_today,'up','近7日新增 '+(a.new_7d||0))+
      card('手机号填写',a.with_phone,'blue',rate(a.with_phone,a.total)+' 填写率')+
      card('邮箱填写',a.with_email,'purple',rate(a.with_email,a.total)+' 填写率')+
    '</div>'+
    '<div class="panel"><h2>👤 账号体系 · 角色分布 / 最近注册</h2>'+
      '<div class="sub" style="margin-bottom:10px">管理员 '+(by.admin||0)+' · 分析师 '+(by.analyst||0)+' · 访客 '+(by.viewer||0)+'</div>'+
      '<table><thead><tr><th>账号</th><th>角色</th><th>手机</th><th>邮箱</th><th style="text-align:right">注册时间</th></tr></thead><tbody>'+acctRows+'</tbody></table>'+
    '</div>'
  ) : '';
  // 拉新 / 邀请
  const iv=d.invites;
  const ivTop = iv&&iv.top&&iv.top.length ? iv.top.map(function(t,i){ return '<tr><td class="rk">'+(i+1)+'</td><td class="t">'+esc(t.username)+'</td><td class="c">'+t.count+' 人</td></tr>'; }).join('') : '<tr><td colspan=3 class="sub" style="padding:12px">暂无邀请记录</td></tr>';
  const invHtml = iv ? (
    '<div class="grid">'+
      card('邀请注册',iv.invited_total,'up','今日 +'+(iv.invited_today||0)+' · 近7日 +'+(iv.invited_7d||0))+
      card('邀请占比',(iv.invite_rate||0)+'%','amber','经邀请注册 / 总注册')+
      card('有效邀请人',iv.inviters,'blue','至少成功邀请 1 人')+
    '</div>'+
    '<div class="panel"><h2>🎁 邀请榜 · 谁拉的人最多</h2><table><tbody>'+ivTop+'</tbody></table></div>'
  ) : '';
  $('#app').innerHTML=
    '<h1>DEEPFOCUS 金融终端 · 数据看板 <button class="refresh" onclick="load()">↻ 刷新</button></h1>'+
    '<div class="sub">更新于 '+esc((d.generated_at||'').replace('T',' ').slice(0,19))+'（各表时间为北京时间）</div>'+
    // 顶部分区导航（粘性，▸=可折叠抽屉）
    '<div class="nav">'+
      '<a href="#top">⭐ 核心</a>'+
      '<a href="#g-biz">👑 会员·收入</a>'+
      '<a href="#g-ops">🛠 运营待办</a>'+
      '<a href="#g-today">📊 概览明细 ▸</a>'+
      '<a href="#g-ai">🤖 AI增长 ▸</a>'+
      '<a href="#g-growth">📈 增长明细 ▸</a>'+
      '<a href="#g-use">🔥 使用·内容 ▸</a>'+
    '</div>'+
    // ===== 第一屏：北极星 6 大指标（重心）=====
    '<div id="top" class="anchor"></div>'+
    '<div class="kpi">'+
      kpi('今日访问', ty, dayArrow, '昨日 '+yd+' · 累计 '+(d.pageviews||0), 'amber')+
      kpi('今日新增注册', (a&&a.new_today)||0, '', '近7日 +'+((a&&a.new_7d)||0)+' · 累计 '+((a&&a.total)||0), 'up')+
      kpi('今日邀请注册', (iv&&iv.invited_today)||0, '', '占比 '+((iv&&iv.invite_rate)||0)+'% · 累计 '+((iv&&iv.invited_total)||0), 'blue')+
      '<div class="kpit" id="kpi-paid"><div class="kpit-k">付费会员</div><div class="kpit-v amber">…</div><div class="kpit-s">加载中…</div></div>'+
      '<div class="kpit" id="kpi-active"><div class="kpit-k">今日活跃会员</div><div class="kpit-v up">…</div><div class="kpit-s">加载中…</div></div>'+
      '<div class="kpit" id="kpi-expire"><div class="kpit-k">7日待续费</div><div class="kpit-v warn">…</div><div class="kpit-s">加载中…</div></div>'+
    '</div>'+
    // ⚡ 需要你处理：动作队列（异步填充计数）
    '<div class="todo">'+
      '<a class="pill" id="pill-dm" href="#g-ops"><b>·</b><span class="pl">未读私信</span></a>'+
      '<a class="pill" id="pill-redeem" href="#g-biz"><b>·</b><span class="pl">待人工兑换</span></a>'+
      '<a class="pill" id="pill-expire" href="#g-biz"><b>·</b><span class="pl">7 天内到期会员</span></a>'+
      '<a class="pill" id="pill-src" href="#g-ops"><b>·</b><span class="pl">研报源状态</span></a>'+
    '</div>'+
    // ===== 概览明细（折叠）=====
    grpD('g-today','📊 经营概览明细','点开 · 流量 / 互动 / 转化拆分')+
    '<div class="grid">'+
      card('累计访问量',d.pageviews,'amber','今日 '+ty+' '+dayArrow+' · 昨日 '+yd)+
      card('总互动',d.interactions,'up','AI'+(d.ai_total||0)+' · 复制'+(d.copy_total||0)+' · 下载'+(d.research_downloads||0))+
      card('AI 解读',d.ai_total,'up','研报 '+(d.ai_research||0)+' · 文章 '+(d.ai_news||0))+
      card('复制次数',d.copy_total,'blue','图 '+(d.copy_image||0)+' · 文 '+(d.copy_text||0)+' · 快讯 '+(d.copy_news||0))+
      card('研报打开',d.research_downloads,'purple','')+
    '</div>'+
    '<div class="grid">'+
      card('AI 解读率',rate(d.ai_total,d.pageviews),'up','解读次数 / 访问量')+
      card('复制转化率',rate(d.copy_total,d.pageviews),'blue','复制次数 / 访问量')+
      card('人均互动',d.pageviews?(d.interactions/d.pageviews).toFixed(2):'—','amber','总互动 / 访问量')+
    '</div></details>'+
    // ===== AI 增长分析（折叠）=====
    grpD('g-ai','🤖 AI 增长分析','点开 · 每日16:20自动 · 日活/留存/付费转化/改进建议')+
    '<div id="growth"><div class="sub">增长分析加载中…</div></div></details>'+
    // ===== 增长 · 拉新明细（折叠）=====
    grpD('g-growth','📈 增长 · 拉新明细','点开 · 注册账号 / 邀请榜')+
    acctHtml+
    invHtml+
    '</details>'+
    // ===== 会员·收入（业务核心） =====
    grp('g-biz','👑 会员 · 收入','付费会员 / 到期续费 / 收款 / 兑换码 / 邀请奖励')+
    '<div id="members"><div class="sub">会员数据加载中…</div></div>'+
    '<div id="referrals"><div class="sub">邀请活动加载中…</div></div>'+
    '<div id="pay"><div class="panel"><h2>💰 收款设置</h2><div class="sub">加载中…</div></div></div>'+
    '<div id="codes"><div class="panel"><h2>🎟️ 会员兑换码</h2><div class="sub">加载中…</div></div></div>'+
    // ===== 使用 · 内容（次要分析，默认折叠，点开才看） =====
    '<details class="fold"><summary class="group anchor" id="g-use"><h2>🔥 使用 · 内容</h2><span class="gs">点开展开 · 趋势 / 时段 / 设备 / 榜单</span></summary>'+
    '<div class="panel"><h2>📈 近 '+(tr.length||0)+' 日趋势</h2>'+
      '<div class="legend"><span><i style="background:#ffb000"></i>访问</span><span><i style="background:#2bd96a"></i>AI解读</span><span><i style="background:#6ab0ff"></i>复制</span></div>'+
      '<div class="gbars">'+gbars+'</div></div>'+
    '<div class="two">'+
      '<div class="panel"><h2>🕘 时段活跃（按小时）</h2><div class="hbars">'+hbars+'</div></div>'+
      '<div class="panel"><h2>📱 移动 / 桌面 占比</h2>'+devBar+'</div>'+
    '</div>'+
    '<div class="two">'+
      '<div class="panel"><h2>📄 研报打开榜</h2><table><tbody>'+lb(d.top_reports)+'</tbody></table></div>'+
      '<div class="panel"><h2>🧠 AI 解读榜</h2><table><tbody>'+lb(d.top_ai)+'</tbody></table></div>'+
    '</div>'+
    '<div class="panel"><h2>🔥 单条快讯/文章热度榜（复制 + AI 解读）</h2><table><tbody>'+lb(d.top_news)+'</tbody></table></div>'+
    '</details>'+
    // ===== 运营待办 =====
    grp('g-ops','🛠 运营待办','用户私信 / 研报源登录态 / 操作流水')+
    '<div id="dm"><div class="panel"><h2>💬 用户私信</h2><div class="sub">加载中…</div></div></div>'+
    '<div id="zsxq"><div class="sub">研报源状态加载中…</div></div>'+
    '<div id="pkeys"><div class="panel"><h2>🔌 合作方 API</h2><div class="sub">加载中…</div></div></div>'+
    '<div id="act"><div class="sub">操作流水加载中…</div></div>'+
    '<div id="rq"><div class="panel"><h2>🤖 复盘 AI 质量</h2><div class="sub">加载中…</div></div></div>'+
    '<div class="ft">DEEPFOCUS 金融终端 · 内部数据，请勿外传</div>';
  loadGrowth();
  loadActivity('');
  loadReferrals();
  loadZsxq();
  loadPartnerKeys();
  loadDM();
  loadMembers();
  loadCodes();
  loadPay();
  loadReviewQuality();
}
// ===== AI 增长分析：KPI 卡 + DAU 趋势 + AI 报告 =====
async function loadGrowth(){
  let d;
  try{ const r=await fetch('/api/metrics/growth?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); d=await r.json(); }
  catch(e){ const el=$('#growth'); if(el) el.innerHTML='<div class="err">增长分析读取失败('+e.message+')</div>'; return; }
  const k=d.kpis||{}; const u=k.users||{}; const dau=k.dau||{}; const ret=k.retention||{}; const mon=k.monetization||{};
  const fun=mon.funnel||{};
  const pct=v=>v==null?'—':v+'%';
  // DAU 趋势条（登录用户=绿 叠 匿名=蓝）
  const sr=dau.series||[]; const dmx=Math.max(1,...sr.map(x=>x.total));
  const dbars=sr.map(x=>'<div class="gday" title="'+x.day+'  登录'+x.users+' · 匿名'+x.anon+'"><div class="gset">'+
    '<div class="gb" style="height:'+Math.round(x.users/dmx*100)+'%;background:#2bd96a"></div>'+
    '<div class="gb" style="height:'+Math.round(x.anon/dmx*100)+'%;background:#6ab0ff"></div>'+
    '</div><div class="gd">'+(x.day||'').slice(5)+'</div></div>').join('')||'<div class="sub">暂无数据</div>';
  // 漏斗
  const fmax=Math.max(1,fun.visitors||0);
  const fstep=(label,v,color)=>'<div style="margin-bottom:6px"><div style="display:flex;justify-content:space-between;font-size:11px"><span class="sub">'+label+'</span><b style="color:#e6ebf2">'+(v||0)+'</b></div><div style="height:8px;background:#0c1018;border-radius:4px;overflow:hidden"><div style="height:100%;width:'+Math.max(2,Math.round((v||0)/fmax*100))+'%;background:'+color+'"></div></div></div>';
  // AI 报告
  const rep=(d.latest&&d.latest.report)||null; const prov=(d.latest&&d.latest.provider)||'';
  const li=a=>(a&&a.length)?('<ul style="margin:4px 0 0;padding-left:18px;line-height:1.8">'+a.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'):'<div class="sub">—</div>';
  const repHtml = rep? (
    '<div class="panel"><h2>🧠 AI 分析报告 <span class="sub" style="font-weight:400">'+esc((d.latest.day||''))+' · '+(prov==='llm'?'AI 生成':'规则模板(AI不可用回退)')+'</span>'+
    '<button class="refresh" id="growthRegen">↻ 重新分析</button></h2>'+
    '<div style="font-size:13.5px;line-height:1.8;color:#e6ebf2;margin-bottom:10px">'+esc(rep.summary||'')+'</div>'+
    '<div class="two">'+
      '<div><div class="sub" style="color:#2bd96a;font-weight:700">✅ 亮点</div>'+li(rep.highlights)+
      '<div class="sub" style="color:#ff5a52;font-weight:700;margin-top:10px">⚠️ 风险</div>'+li(rep.risks)+'</div>'+
      '<div><div class="sub" style="color:var(--amber);font-weight:700">🎯 改进动作（按优先级）</div>'+li(rep.actions)+'</div>'+
    '</div></div>'
  ) : '<div class="panel"><h2>🧠 AI 分析报告</h2><div class="sub">暂无报告（每日 16:20 自动生成）</div><button class="refresh" id="growthRegen">立即生成</button></div>';
  $('#growth').innerHTML=
    '<div class="grid">'+
      card('今日 DAU',dau.today,'up','周活 '+(dau.wau||0)+'（登录+匿名去重）')+
      card('次日留存',pct(ret.d1&&ret.d1.rate),'amber','样本 '+((ret.d1&&ret.d1.cohort)||0)+' 人 · 回访 '+((ret.d1&&ret.d1.retained)||0))+
      card('7日留存',pct(ret.d7&&ret.d7.rate),'blue','样本 '+((ret.d7&&ret.d7.cohort)||0)+' 人')+
      card('付费转化率',pct(mon.paid_rate_pct),'purple','付费会员 '+(mon.paid_members||0)+' / 总用户 '+(u.total||0))+
    '</div>'+
    '<div class="two">'+
      '<div class="panel"><h2>📈 近 '+sr.length+' 日 DAU</h2>'+
        '<div class="legend"><span><i style="background:#2bd96a"></i>登录用户</span><span><i style="background:#6ab0ff"></i>匿名访客</span></div>'+
        '<div class="gbars">'+dbars+'</div></div>'+
      '<div class="panel"><h2>🪜 14 日转化漏斗（去重人数）</h2>'+
        fstep('访问',fun.visitors,'#6ab0ff')+
        fstep('点邀请',fun.invite_click,'#c4b5fd')+
        fstep('领体验卡',fun.claim_trial,'#2bd96a')+
        fstep('打开购买页',fun.open_buy,'#ffb000')+
        fstep('点「我已付款」',fun.buy_contact,'#ff5a52')+
      '</div>'+
    '</div>'+repHtml;
  var rg=$('#growthRegen'); if(rg) rg.onclick=async function(){ rg.disabled=true; rg.textContent='分析中…(约30秒)';
    try{ const r=await fetch('/api/admin/growth-analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token})}); if(!r.ok) throw new Error(r.status); loadGrowth(); }
    catch(e){ alert('分析失败：'+e.message); rg.disabled=false; rg.textContent='↻ 重新分析'; } };
}
// ===== 收款设置：套餐价格 + 收款码上传 =====
async function loadPay(){
  let c;
  try{ const r=await fetch('/api/payment-config'); if(!r.ok) throw new Error(r.status); c=await r.json(); }
  catch(e){ const el=$('#pay'); if(el) el.innerHTML='<div class="panel"><h2>💰 收款设置</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const pkgs=c.packages||[];
  const rows=pkgs.map((p,i)=>'<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><span style="width:64px" class="sub">'+esc(p.label)+'</span>'+
    '<span class="sub">'+p.days+'天 · ¥</span><input data-i="'+i+'" class="payPrice" type="number" value="'+p.price+'" style="width:80px;background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:5px"></div>').join('');
  const qrBox=(w,label)=>{ const has=c[w]; return '<div style="text-align:center"><div class="sub" style="margin-bottom:4px">'+label+'</div>'+
    (has?'<img src="/api/payment-qr/'+w+'?t='+Date.now()+'" style="width:120px;height:120px;object-fit:contain;background:#fff;border-radius:6px">':'<div style="width:120px;height:120px;display:flex;align-items:center;justify-content:center;border:1px dashed var(--line);border-radius:6px;color:var(--mute);font-size:12px">未上传</div>')+
    '<div style="margin-top:6px"><input type="file" accept="image/*" id="qr_'+w+'" style="display:none"><button onclick="document.getElementById(\\'qr_'+w+'\\').click()" style="background:rgba(106,176,255,.14);color:#9fd0ff;border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer;font-family:inherit;font-size:12px">'+(has?'更换':'上传')+'</button></div></div>'; };
  $('#pay').innerHTML='<div class="panel"><h2>💰 收款设置 <span class="sub" style="font-weight:400">用户购买页展示</span></h2>'+
    '<div class="two" style="grid-template-columns:1fr 1fr"><div><div class="sub" style="margin-bottom:8px">套餐价格（元）</div>'+rows+
    '<div class="sub" style="margin:10px 0 4px">购买说明</div><textarea id="payNote" style="width:100%;box-sizing:border-box;height:60px;background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px;font-family:inherit;font-size:12px">'+esc(c.note||'')+'</textarea>'+
    '<button id="paySave" style="margin-top:8px;background:var(--amber);color:#000;border:none;border-radius:6px;font-weight:700;padding:7px 18px;cursor:pointer;font-family:inherit">保存价格/说明</button></div>'+
    '<div style="display:flex;gap:14px;justify-content:center;align-items:flex-start;padding-top:6px">'+qrBox('wechat','微信收款码')+qrBox('alipay','支付宝收款码')+'</div></div></div>';
  $('#paySave').onclick=savePay;
  ['wechat','alipay'].forEach(w=>{ var inp=$('#qr_'+w); if(inp) inp.onchange=function(){ uploadQR(w, this.files[0]); }; });
}
async function savePay(){
  const btn=$('#paySave'); btn.disabled=true; btn.textContent='…';
  const cur=await (await fetch('/api/payment-config')).json();
  const pkgs=(cur.packages||[]).map((p,i)=>({ ...p, price: parseInt(document.querySelector('.payPrice[data-i="'+i+'"]').value)||p.price }));
  try{
    const r=await fetch('/api/admin/payment-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({packages:pkgs, note:$('#payNote').value, token:token})});
    if(!r.ok) throw new Error(r.status); btn.textContent='✓ 已保存'; setTimeout(loadPay,800);
  }catch(e){ alert('保存失败：'+e.message); btn.disabled=false; btn.textContent='保存价格/说明'; }
}
async function uploadQR(which, f){
  if(!f){ alert('没拿到文件，请重新选择'); return; }
  if(f.size > 6*1024*1024){ alert('图片过大（>6MB），请压缩后再传'); return; }
  try{
    const buf = await f.arrayBuffer();              // 比 FileReader 更稳
    const bytes = new Uint8Array(buf);
    let bin=''; const CH=0x8000;
    for(let i=0;i<bytes.length;i+=CH){ bin += String.fromCharCode.apply(null, bytes.subarray(i, i+CH)); }
    const b64 = btoa(bin);                          // 转 base64，走 JSON 上传（WAF 不拦）
    const r=await fetch('/api/admin/payment-qr',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({which:which, image:b64, token:token})});
    if(!r.ok){ let d=''; try{ d=(await r.json()).detail; }catch(e){} throw new Error(d||('HTTP '+r.status)); }
    loadPay();
  }catch(e){ alert('上传失败：'+(e&&e.message||e)); }
}
// ===== 会员兑换码：生成 + 复制 + 列表统计 =====
async function loadCodes(){
  let data;
  try{ const r=await fetch('/api/admin/codes?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); data=await r.json(); }
  catch(e){ const el=$('#codes'); if(el) el.innerHTML='<div class="panel"><h2>🎟️ 会员兑换码</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const s=data.stats||{total:0,used:0,unused:0}; const codes=data.codes||[];
  const rows = codes.slice(0,40).map(c=>{
    const badge = c.used? '<span style="color:#ff5a52">已用·'+esc(c.used_by_name||'')+'</span>' : '<span style="color:#2bd96a">未用</span>';
    const tier = c.kind==='trial' ? '<span style="color:#6ad0ff">体验·'+c.days+'天</span>' : (c.tier==='lifetime'?'永久':(c.days+'天'));
    return '<tr><td style="font-family:monospace;letter-spacing:1px">'+esc(c.code)+'</td><td>'+tier+'</td><td class="sub">'+esc(c.note||'')+'</td><td>'+badge+'</td></tr>';
  }).join('')||'<tr><td colspan=4 class="sub" style="padding:10px">还没有兑换码，用上面表单生成</td></tr>';
  $('#codes').innerHTML='<div class="panel"><h2>🎟️ 会员兑换码 <span class="sub" style="font-weight:400">共 '+s.total+' · 未用 '+s.unused+' · 已用 '+s.used+'</span></h2>'+
    '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">'+
      '<label class="sub">类型<br><select id="cTier" style="background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px"><option value="premium">尊享(限时)</option><option value="trial">体验周卡·每天限1张</option><option value="lifetime">永久</option></select></label>'+
      '<label class="sub">天数<br><select id="cDays" style="background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px"><option value="30">30 月卡</option><option value="90">90 季卡</option><option value="180">180 半年卡</option><option value="365">365 年卡</option></select> <span class="sub" style="font-size:11px">(体验周卡固定7天·永久忽略)</span></label>'+
      '<label class="sub">数量<br><input id="cCount" type="number" value="10" min="1" max="500" style="width:70px;background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px"></label>'+
      '<label class="sub">备注<br><input id="cNote" placeholder="如 月卡批次1" style="width:130px;background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px"></label>'+
      '<button id="cGen" style="background:var(--amber);color:#000;border:none;border-radius:6px;font-weight:700;padding:7px 16px;cursor:pointer;font-family:inherit">生成</button>'+
    '</div>'+
    '<div id="cOut"></div>'+
    '<table style="margin-top:6px"><thead><tr><th>兑换码</th><th>类型</th><th>备注</th><th>状态</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
  $('#cGen').onclick=genCodes;
}
async function genCodes(){
  const btn=$('#cGen'); btn.disabled=true; btn.textContent='…';
  const ctier=$('#cTier').value; let cdays=parseInt($('#cDays').value)||30; if(ctier==='trial') cdays=7;
  const payload={ tier:ctier, days:cdays, count:parseInt($('#cCount').value)||10, note:$('#cNote').value||'', token:token };
  try{
    const r=await fetch('/api/admin/codes/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok) throw new Error(r.status); const d=await r.json();
    const txt=(d.codes||[]).join('\\n');
    const h=Math.min(160,24+(d.count*18));
    $('#cOut').innerHTML='<div style="background:#0c1018;border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:10px">'+
      '<div class="sub" style="margin-bottom:6px">✅ 已生成 '+d.count+' 个（'+(d.tier==='trial'?('体验周卡·'+d.days+'天·每人每天限领1张'):(d.tier==='lifetime'?'永久':d.days+'天'))+'），复制发给用户即可兑换：</div>'+
      '<textarea readonly style="width:100%;box-sizing:border-box;height:'+h+'px;background:#06080c;color:#9fd0ff;border:1px solid var(--line);border-radius:6px;font-family:monospace;font-size:12px;padding:8px;letter-spacing:1px">'+esc(txt)+'</textarea>'+
      '<button id="cCopy" style="margin-top:6px;background:rgba(106,176,255,.14);color:#9fd0ff;border:1px solid var(--line);border-radius:6px;padding:5px 12px;cursor:pointer;font-family:inherit;font-size:12px">复制全部</button></div>';
    var cc=$('#cCopy'); if(cc) cc.onclick=function(){ var ta=this.previousElementSibling; if(navigator.clipboard){ navigator.clipboard.writeText(ta.value).then(()=>{cc.textContent='✓ 已复制';}); } else { ta.select(); document.execCommand('copy'); cc.textContent='✓ 已复制'; } };
    loadCodes();
  }catch(e){ alert('生成失败：'+e.message); }
  finally{ btn.disabled=false; btn.textContent='生成'; }
}
// ===== 合作方 API：发钥（名称/套餐/有效期/总次数/每日次数）+ 列表用量 + 吊销 =====
async function loadPartnerKeys(){
  let data;
  try{ const r=await fetch('/api/admin/partner-keys?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); data=await r.json(); }
  catch(e){ const el=$('#pkeys'); if(el) el.innerHTML='<div class="panel"><h2>🔌 合作方 API</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const keys=data.keys||[]; const u=data.usage||{total:0,today:0}; const bill=data.billing||{}; const al=data.alerts||{counts:{}};
  const inp='background:#0c0d12;color:#e6ebf2;border:1px solid var(--line);border-radius:6px;padding:6px';
  const yuan=c=>'¥'+(((c||0)/100)).toLocaleString('zh-CN');
  const bstat={paid:'<span style="color:#2bd96a">已收款</span>',unpaid:'<span style="color:#ffb000">待收款</span>',overdue:'<span style="color:#ff5a52">逾期</span>',comp:'<span class="sub">赠送</span>'};
  const rows = keys.map(k=>{
    const st = k.active? '<span style="color:#2bd96a">启用</span>' : '<span style="color:#ff5a52">已吊销</span>';
    const exp = k.expires_at? esc(String(k.expires_at).slice(0,10)) : '永久';
    const mc = k.max_calls? (k.call_count+' / '+k.max_calls) : (k.call_count+' / ∞');
    const dqpct = k.daily_quota? Math.round((k.today||0)*100/k.daily_quota) : 0;
    const dq = k.daily_quota? ((k.today||0)+'/'+k.daily_quota+(dqpct>=80?' <span style="color:#ff5a52">'+dqpct+'%</span>':'')) : '不限';
    const bz = (k.price_cents? yuan(k.price_cents)+(k.billing_period==='yearly'?'/年':k.billing_period==='monthly'?'/月':''):'—')+' '+(bstat[k.billing_status]||'');
    const last = k.last_used_at? esc(String(k.last_used_at).slice(0,16).replace('T',' ')) : '—';
    let ops='';
    if(k.active){
      if(k.billing_status==='unpaid') ops+='<button data-pfx="'+esc(k.key_prefix)+'" class="pkPaid" style="background:rgba(43,217,106,.14);color:#7fe0a4;border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer;font-family:inherit;font-size:11px;margin-right:4px">标记已收款</button>';
      ops+='<button data-pfx="'+esc(k.key_prefix)+'" data-per="'+esc(k.billing_period||'')+'" class="pkRenew" style="background:rgba(106,176,255,.14);color:#9fd0ff;border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer;font-family:inherit;font-size:11px;margin-right:4px">续期</button>';
      ops+='<button data-pfx="'+esc(k.key_prefix)+'" class="pkRev" style="background:rgba(255,90,82,.14);color:#ff8a82;border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer;font-family:inherit;font-size:11px">吊销</button>';
    }
    return '<tr><td style="font-family:monospace">'+esc(k.key_prefix)+'…</td><td>'+esc(k.name)+'</td><td class="sub">'+esc(k.tier)+'·'+(k.rate_per_min||60)+'/分</td><td>'+mc+'</td><td class="sub">'+dq+'</td><td class="sub">'+exp+'</td><td>'+bz+'</td><td class="sub">'+last+'</td><td>'+st+'</td><td>'+ops+'</td></tr>';
  }).join('')||'<tr><td colspan=10 class="sub" style="padding:10px">还没有合作方密钥，用上面表单签发</td></tr>';
  const cnt=al.counts||{};
  const alertPills=[
    cnt.near_quota?('<span style="background:rgba(255,90,82,.15);color:#ff8a82;border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px">⚠ '+cnt.near_quota+' 个近配额·建议升档</span>'):'',
    cnt.near_expiry?('<span style="background:rgba(255,176,0,.15);color:#ffce72;border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px">⏰ '+cnt.near_expiry+' 个近到期·续费机会</span>'):'',
    cnt.expired?('<span style="background:rgba(255,90,82,.15);color:#ff8a82;border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px">⛔ '+cnt.expired+' 个已过期断流</span>'):'',
    cnt.unpaid_overdue?('<span style="background:rgba(255,176,0,.15);color:#ffce72;border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px">💰 '+cnt.unpaid_overdue+' 个待收款超期</span>'):'',
  ].filter(Boolean).join(' ');
  $('#pkeys').innerHTML='<div class="panel"><h2>🔌 合作方 API <span class="sub" style="font-weight:400">密钥 '+keys.length+' · 累计调用 '+(u.total||0)+' · 今日 '+(u.today||0)+'</span></h2>'+
    '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px;font-size:13px">'+
      '<span>💰 本月已收 <b style="color:#2bd96a">'+yuan(bill.paid_month_cents)+'</b></span>'+
      '<span class="sub">年度累计 '+yuan(bill.paid_year_cents)+'</span>'+
      '<span class="sub">待收款 '+yuan(bill.unpaid_cents)+'（'+(bill.unpaid_keys||0)+' 个）</span>'+
    '</div>'+
    (alertPills?('<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">'+alertPills+'</div>'):'')+
    '<div class="sub" style="margin-bottom:8px;font-size:11px">只开放自有内容(复盘/速判卡/快讯/文章/研报)；密钥只在签发时完整显示一次，库内仅存摘要。计费为账面记录+人工收款对账，系统不扣费。合作方用 Header <code>X-API-Key</code> 调用 <code>/api/v1/*</code>，文档 <code>/api/v1/docs</code>。</div>'+
    '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">'+
      '<label class="sub">合作方名称<br><input id="pkName" placeholder="如 某某券商" style="width:130px;'+inp+'"></label>'+
      '<label class="sub">套餐<br><select id="pkTier" style="'+inp+'"><option value="trial">trial·30/分</option><option value="basic" selected>basic·60/分</option><option value="pro">pro·300/分</option></select></label>'+
      '<label class="sub">有效期(天)<br><input id="pkExp" type="number" min="0" placeholder="0=永久" style="width:80px;'+inp+'"></label>'+
      '<label class="sub">总次数<br><input id="pkMax" type="number" min="0" placeholder="0=不限" style="width:90px;'+inp+'"></label>'+
      '<label class="sub">每日次数<br><input id="pkDaily" type="number" min="0" placeholder="0=不限" style="width:80px;'+inp+'"></label>'+
      '<label class="sub">价格(元)<br><input id="pkPrice" type="number" min="0" placeholder="0=免费" style="width:80px;'+inp+'"></label>'+
      '<label class="sub">周期<br><select id="pkPeriod" style="'+inp+'"><option value="">—</option><option value="monthly">月</option><option value="yearly">年</option><option value="oneoff">一次性</option></select></label>'+
      '<button id="pkGen" style="background:var(--amber);color:#000;border:none;border-radius:6px;font-weight:700;padding:7px 16px;cursor:pointer;font-family:inherit">签发密钥</button>'+
    '</div>'+
    '<div id="pkOut"></div>'+
    '<table style="margin-top:6px"><thead><tr><th>密钥</th><th>合作方</th><th>套餐</th><th>已用/总</th><th>今日/日限</th><th>到期</th><th>计费</th><th>最近调用</th><th>状态</th><th>操作</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
  $('#pkGen').onclick=genPartnerKey;
  document.querySelectorAll('.pkRev').forEach(b=>{ b.onclick=function(){ revokePartnerKey(this.getAttribute('data-pfx')); }; });
  document.querySelectorAll('.pkPaid').forEach(b=>{ b.onclick=function(){ markPaidKey(this.getAttribute('data-pfx')); }; });
  document.querySelectorAll('.pkRenew').forEach(b=>{ b.onclick=function(){ renewKey(this.getAttribute('data-pfx'), this.getAttribute('data-per')); }; });
}
async function markPaidKey(pfx){
  const note=prompt('标记「'+pfx+'…」已收款。可填收款备注(方式/流水号/对接人)：','');
  if(note===null) return;
  try{ const r=await fetch('/api/admin/partner-keys/'+encodeURIComponent(pfx)+'/billing?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'mark_paid',billing_note:note})}); if(!r.ok) throw new Error(r.status); loadPartnerKeys(); }
  catch(e){ alert('操作失败：'+e.message); }
}
async function renewKey(pfx, per){
  const def = per==='yearly'?'366':per==='monthly'?'31':'31';
  const days=prompt('续期「'+pfx+'…」：在原到期日上延长多少天？(保留同一密钥，合作方无需改配置)', def);
  if(days===null) return; const d=parseInt(days)||0; if(d<=0){ alert('请输入正整数天数'); return; }
  try{ const r=await fetch('/api/admin/partner-keys/'+encodeURIComponent(pfx)+'/renew?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days:d})}); if(!r.ok) throw new Error(r.status); loadPartnerKeys(); }
  catch(e){ alert('续期失败：'+e.message); }
}
async function genPartnerKey(){
  const btn=$('#pkGen'); btn.disabled=true; btn.textContent='…';
  const payload={ name:$('#pkName').value||'', tier:$('#pkTier').value, expires_in_days:parseInt($('#pkExp').value)||0, max_calls:parseInt($('#pkMax').value)||0, daily_quota:parseInt($('#pkDaily').value)||0, price_cents:Math.round((parseFloat($('#pkPrice').value)||0)*100), billing_period:$('#pkPeriod').value||'' };
  try{
    const r=await fetch('/api/admin/partner-keys?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok) throw new Error(r.status); const d=await r.json();
    $('#pkOut').innerHTML='<div style="background:#0c1018;border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:10px">'+
      '<div class="sub" style="margin-bottom:6px">✅ 已签发给「'+esc(d.name)+'」（'+esc(d.tier)+'）。<b style="color:#ff8a82">完整密钥只显示这一次</b>，复制发给合作方，关掉就看不到了：</div>'+
      '<textarea readonly style="width:100%;box-sizing:border-box;height:46px;background:#06080c;color:#9fd0ff;border:1px solid var(--line);border-radius:6px;font-family:monospace;font-size:13px;padding:8px">'+esc(d.key)+'</textarea>'+
      '<button id="pkCopy" style="margin-top:6px;background:rgba(106,176,255,.14);color:#9fd0ff;border:1px solid var(--line);border-radius:6px;padding:5px 12px;cursor:pointer;font-family:inherit;font-size:12px">复制密钥</button></div>';
    var pc=$('#pkCopy'); if(pc) pc.onclick=function(){ var ta=this.previousElementSibling; if(navigator.clipboard){ navigator.clipboard.writeText(ta.value).then(()=>{pc.textContent='✓ 已复制';}); } else { ta.select(); document.execCommand('copy'); pc.textContent='✓ 已复制'; } };
    loadPartnerKeys();
  }catch(e){ alert('签发失败：'+e.message); }
  finally{ btn.disabled=false; btn.textContent='签发密钥'; }
}
async function revokePartnerKey(pfx){
  if(!confirm('确认吊销密钥 '+pfx+'…？吊销后该合作方立即无法调用。')) return;
  try{
    const r=await fetch('/api/admin/partner-keys/'+encodeURIComponent(pfx)+'?token='+encodeURIComponent(token),{method:'DELETE'});
    if(!r.ok) throw new Error(r.status);
    loadPartnerKeys();
  }catch(e){ alert('吊销失败：'+e.message); }
}
// ===== 用户私信：会话列表 + 点开看对话 + 回复（壳/消息分离，支持自动轮询不清空输入框）=====
let _dmCur='', _dmTitle='', _dmPoll=null, _dmLastUnread=0, _dmBaseTitle=document.title;
async function loadDM(){
  // 渲染面板骨架一次，再填列表；首次设置轮询
  $('#dm').innerHTML='<div class="panel"><h2 id="dmh">💬 用户私信</h2>'+
    '<div class="two" style="grid-template-columns:1fr 1.3fr"><div id="dmlist" style="max-height:440px;overflow:auto">'+
    '<div class="sub" style="padding:12px">加载中…</div></div>'+
    '<div id="dmthread"><div class="sub" style="padding:12px">← 点左侧用户查看并回复</div></div></div></div>';
  await refreshDMList();
  if(!_dmPoll) _dmPoll=setInterval(pollDM, 15000);
}
async function loadReviewQuality(){
  let d;
  try{ const r=await fetch('/api/metrics/review-quality?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); d=await r.json(); }
  catch(e){ const el=$('#rq'); if(el) el.innerHTML='<div class="panel"><h2>🤖 复盘 AI 质量</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const rec=d.recent||[];
  const tr=d.trend||{};
  // 环比箭头：按 good 着色（改善=绿/变差=红/持平=灰），与指标方向无关
  const arrow=(t,isRate)=>{ if(!t||t.cur==null||t.prev==null) return '<span class="sub" style="font-size:10px">环比无</span>';
    const c=t.good===true?'#2bd96a':(t.good===false?'#ff5a52':'#8a94a3');
    const a=t.dir==='down'?'↓':(t.dir==='up'?'↑':'→');
    const dv=isRate? ((t.delta>0?'+':'')+Math.round(t.delta*100)+'pp') : ((t.delta>0?'+':'')+t.delta);
    return '<span style="color:'+c+';font-size:11px;font-weight:700">'+a+' '+dv+'</span> <span class="sub" style="font-size:10px">vs前7天</span>'; };
  const kpi=(label,val,hint,t,isRate)=>'<div style="flex:1;min-width:120px;background:#11161f;border:1px solid var(--line);border-radius:8px;padding:8px 10px"><div class="sub" style="font-size:11px">'+label+'</div><div style="font-size:20px;font-weight:800;color:#e6ebf2">'+val+'</div><div style="font-size:10px;margin-top:2px">'+(t?arrow(t,isRate):'<span class="sub">'+(hint||'')+'</span>')+'</div></div>';
  const rows = rec.length? rec.map(function(x){
    const sess = x.session==='midday'?'午盘':'收盘';
    const after = x.numviol_after>0? '<span style="color:#ff5a52;font-weight:700">'+x.numviol_after+'</span>' : '<span style="color:#2bd96a">0</span>';
    return '<tr><td>'+esc(x.date)+' '+sess+'</td><td>'+x.critic_issues+'</td><td>'+x.numviol_before+'</td><td>'+after+'</td><td>'+(x.revised?'✓':'—')+'</td><td class="sub">'+esc(x.provider)+'</td></tr>';
  }).join('') : '<tr><td colspan=6 class="sub" style="padding:10px">暂无数据（生成一次复盘后出现）</td></tr>';
  $('#rq').innerHTML='<div class="panel"><h2>🤖 复盘 AI 质量 <span class="sub" style="font-weight:400">近 '+d.count+' 次生成</span></h2>'+
    '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">'+
      kpi('初稿问题/次', d.avg_critic_issues, '', tr.critic_issues)+
      kpi('编造数字/次', d.avg_numviol_before, '', tr.numviol_before)+
      kpi('修订率', Math.round((d.revise_rate||0)*100)+'%', '触发自动修订占比')+
      kpi('修订后干净率', Math.round((d.clean_after_rate||0)*100)+'%', '', tr.clean_after, true)+
    '</div>'+
    '<table style="width:100%;font-size:12px"><thead><tr><th>日期/场次</th><th>初稿问题</th><th>编造数字</th><th>残留</th><th>已修订</th><th>来源</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}
async function refreshDMList(){
  let data;
  try{ const r=await fetch('/api/admin/support/threads?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); data=await r.json(); }
  catch(e){ const l=$('#dmlist'); if(l) l.innerHTML='<div class="sub" style="padding:12px">读取失败('+e.message+')</div>'; return; }
  const ths=data.threads||[]; const un=data.unread_total||0;
  setPill('pill-dm', (un>0?'bad':'ok'), un, '未读私信');
  // 标签页标题 + 面板标题显未读
  document.title=(un?'('+un+') ':'')+_dmBaseTitle;
  const h=$('#dmh'); if(h) h.innerHTML='💬 用户私信'+(un?' <span style="background:#ff5a52;color:#fff;border-radius:999px;padding:0 7px;font-size:11px">'+un+'</span>':'');
  _dmLastUnread=un;
  const rows = ths.length? ths.map(t=>{
    const badge = t.unread? '<span style="background:#ff5a52;color:#fff;border-radius:999px;padding:0 6px;font-size:10px;font-weight:700;margin-left:6px">'+t.unread+'</span>':'';
    const who = t.last_sender==='admin'?'我：':'';
    return '<div class="dmrow" data-uid="'+esc(t.user_id)+'" data-uname="'+esc(t.username)+'" style="padding:9px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:7px;cursor:pointer;background:'+(t.user_id===_dmCur?'#1a2330':'transparent')+'">'+
      '<div style="display:flex;justify-content:space-between;align-items:center"><b style="color:#e6ebf2">'+esc(t.username)+badge+'</b><span class="sub" style="font-size:10px">'+tshort(t.last_at)+'</span></div>'+
      '<div class="sub" style="margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+who+esc(t.last)+'</div></div>';
  }).join('') : '<div class="sub" style="padding:12px">暂无用户私信</div>';
  const l=$('#dmlist'); if(l){ l.innerHTML=rows; l.querySelectorAll('.dmrow').forEach(el=>el.onclick=()=>openThread(el.dataset.uid, el.dataset.uname)); }
}
function openThread(uid, uname){
  _dmCur=uid; _dmTitle=uname||uid;
  document.querySelectorAll('.dmrow').forEach(el=>el.style.background=el.dataset.uid===uid?'#1a2330':'transparent');
  // 渲染对话壳（标题 + 消息容器 + 输入框）一次；消息由 loadMsgs 单独填，轮询只更新消息不动输入框
  $('#dmthread').innerHTML='<div id="dmtitle" style="font-weight:700;margin-bottom:8px;color:#c7d2de">与 '+esc(_dmTitle)+' 的对话</div>'+
    '<div id="dmmsgs" style="max-height:330px;overflow:auto;padding:4px 2px;border:1px solid var(--line);border-radius:8px;margin-bottom:9px"><div class="sub" style="padding:12px">加载中…</div></div>'+
    '<div style="display:flex;gap:7px"><textarea id="dmreply" rows="2" placeholder="回复…（Ctrl/⌘+Enter 发送）" style="flex:1;background:#0c0d12;border:1px solid var(--line);border-radius:8px;color:#e6ebf2;font-family:inherit;font-size:13px;padding:8px;resize:vertical"></textarea>'+
    '<button id="dmsend" style="background:var(--amber);color:#000;border:none;border-radius:8px;font-weight:700;padding:0 16px;cursor:pointer;font-family:inherit">发送</button></div>';
  $('#dmsend').onclick=()=>sendReply(uid);
  $('#dmreply').onkeydown=e=>{ if((e.metaKey||e.ctrlKey)&&e.key==='Enter') sendReply(uid); };
  loadMsgs(uid);
}
async function loadMsgs(uid){
  const box=$('#dmmsgs'); if(!box || _dmCur!==uid) return;
  let data;
  try{ const r=await fetch('/api/admin/support/thread?user_id='+encodeURIComponent(uid)+'&token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); data=await r.json(); }
  catch(e){ return; }
  if(_dmCur!==uid) return;
  const ms=data.messages||[];
  if(ms[0]&&ms[0].username){ _dmTitle=ms[0].username; const tt=$('#dmtitle'); if(tt) tt.innerHTML='与 '+esc(_dmTitle)+' 的对话'; }
  const atBottom = box.scrollHeight-box.scrollTop-box.clientHeight < 40;
  box.innerHTML=ms.map(m=>{ const me=m.sender==='admin';
    return '<div style="display:flex;justify-content:'+(me?'flex-end':'flex-start')+';margin:6px 0"><div style="max-width:78%;padding:7px 11px;border-radius:10px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word;background:'+(me?'rgba(255,176,0,.16)':'#1c2530')+';color:'+(me?'#ffce72':'#e6ebf2')+'">'+esc(m.content)+'<div class="sub" style="font-size:9px;margin-top:3px;text-align:right">'+tshort(m.created_at)+'</div></div></div>';
  }).join('')||'<div class="sub" style="padding:12px">暂无消息</div>';
  if(atBottom) box.scrollTop=box.scrollHeight;
}
async function pollDM(){
  await refreshDMList();           // 列表 + 未读（不动右侧输入框）
  if(_dmCur) loadMsgs(_dmCur);     // 当前会话只更新消息气泡，保留你正在打的回复
}
async function sendReply(uid){
  const ta=$('#dmreply'); const content=(ta.value||'').trim(); if(!content) return;
  const btn=$('#dmsend'); btn.disabled=true; btn.textContent='…';
  try{
    const r=await fetch('/api/admin/support/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,content:content,token:token})});
    if(!r.ok) throw new Error(r.status);
    ta.value=''; await loadMsgs(uid); refreshDMList();
  }catch(e){ alert('回复失败：'+e.message); }
  finally{ btn.disabled=false; btn.textContent='发送'; }
}
async function loadZsxq(){
  let z;
  try{ const r=await fetch('/api/research/auth-status?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); z=await r.json(); }
  catch(e){ $('#zsxq').innerHTML='<div class="panel"><h2>📡 研报源</h2><div class="sub">状态读取失败('+e.message+')</div></div>'; return; }
  const ok=z.ok!==false;
  setPill('pill-src', (ok?'ok':'bad'), (ok?'正常':'失效'), '研报源登录态');
  const dot=ok?'<span style="color:#2bd96a">● 正常</span>':'<span style="color:#ff5a52">● 失效，请更换 cookie</span>';
  const lastok=tshort(z.last_ok)||'—';
  const ov=z.override_active?('已用热更新 cookie · '+tshort(z.override_updated_at)):'用服务器 env cookie';
  $('#zsxq').innerHTML=
    '<div class="panel" style="'+(ok?'':'border-color:#5c1d1d;background:#1c1010')+'">'+
      '<h2>📡 研报源（知识星球登录态）'+dot+'</h2>'+
      '<div class="sub" style="margin-bottom:10px">上次正常：'+lastok+' · '+esc(ov)+(z.detail&&!ok?(' · '+esc(String(z.detail).slice(0,80))):'')+'</div>'+
      '<div class="sub" style="margin-bottom:6px">cookie 过期时在此粘贴新的（先验证再生效，无需重启/SSH）：</div>'+
      '<textarea id="zck" placeholder="粘贴新的 zsxq cookie（或整段 curl）…" style="width:100%;min-height:64px;background:#0a0d12;color:#cfe;border:1px solid var(--line);border-radius:8px;padding:8px;font-size:12px;font-family:inherit"></textarea>'+
      '<div style="margin-top:8px"><button class="refresh" onclick="saveCookie()">✔ 验证并更新</button> <button class="refresh" onclick="testEmail()">✉ 发测试邮件</button> <span id="zmsg" class="sub"></span></div>'+
    '</div>';
}
async function testEmail(){
  $('#zmsg').textContent='发送中…';
  try{
    const r=await fetch('/api/research/test-email?token='+encodeURIComponent(token),{method:'POST'});
    const d=await r.json();
    $('#zmsg').innerHTML = d.ok ? '<span style="color:#2bd96a">✓ 测试邮件已发送，请查收</span>' : '<span style="color:#ff5a52">✗ '+esc(d.detail||'失败')+'</span>';
  }catch(e){ $('#zmsg').innerHTML='<span style="color:#ff5a52">✗ '+esc(e.message)+'</span>'; }
}
async function saveCookie(){
  const v=($('#zck')&&$('#zck').value||'').trim();
  if(v.length<10){ $('#zmsg').innerHTML='<span style="color:#ff5a52">cookie 太短</span>'; return; }
  $('#zmsg').textContent='验证中…';
  try{
    const r=await fetch('/api/research/auth?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookie:v})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||r.status);
    $('#zmsg').innerHTML='<span style="color:#2bd96a">✓ '+esc(d.detail||'已更新')+'</span>';
    setTimeout(loadZsxq,800);
  }catch(e){ $('#zmsg').innerHTML='<span style="color:#ff5a52">✗ '+esc(e.message)+'</span>'; }
}
const ACT_LABEL={pageview:'进入页面',login:'登录',logout:'登出',signup:'注册成功',open_report:'打开研报',ai_report:'研报AI解读',ai_news:'文章AI解读',copy:'复制',open_pdf:'看原文PDF',download:'下载',search:'搜索',tab:'切换板块',open_news:'查看资讯',invite_click:'点击邀请得会员',claim_trial:'领取体验会员',open_buy:'💎打开购买会员页',buy_pkg_select:'选套餐',buy_qr_view:'看收款码',buy_close:'关闭购买页',buy_paid_click:'点已完成付款',buy_contact:'💰发凭证联系开通',open_review:'查看复盘',ai_chat:'AI问答提问',weixin_qa:'📱微信AI提问',deep_research_done:'深度研究完成',share_foresight:'分享预判',deep_share_img:'分享深研图',deep_share_text:'分享深研文',ai_share_img:'分享AI解读图',bookmark:'收藏',unbookmark:'取消收藏',select_stock:'下钻个股',watch_add:'加自选',watch_remove:'移除自选',reaction:'资讯表态',weixin_bind:'打开绑定微信',redeem:'兑换会员码',support_msg:'发私信给管理员',open_referral:'打开邀请面板',theme:'切换主题',tts:'语音播报开关'};
function alabel(a){return ACT_LABEL[a]||a;}
function tshort(s){
  if(!s) return '';
  var x=String(s).trim();
  // 后端时间戳为 UTC（_now_iso 带 +00:00；部分为无时区的 naive，按 UTC 处理），统一转北京时间显示
  var hasTz = /[zZ]$/.test(x) || /[+-][0-9][0-9]:?[0-9][0-9]$/.test(x);
  if(!hasTz) x=x.replace(' ','T')+'Z';
  var d=new Date(x);
  if(isNaN(d.getTime())) return String(s).replace('T',' ').slice(5,19);
  var b=new Date(d.getTime()+288e5), p=function(n){return String(n).padStart(2,'0');};
  return p(b.getUTCMonth()+1)+'-'+p(b.getUTCDate())+' '+p(b.getUTCHours())+':'+p(b.getUTCMinutes())+':'+p(b.getUTCSeconds());
}
async function loadActivity(actor){
  let a;
  try{ const r=await fetch('/api/metrics/activity?token='+encodeURIComponent(token)+(actor?'&actor='+encodeURIComponent(actor):'')+'&limit=300'); if(!r.ok) throw new Error(r.status); a=await r.json(); }
  catch(e){ $('#act').innerHTML='<div class="panel"><h2>🧭 操作流水</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const s=a.stats||{};
  const byAct=Object.entries(s.by_action||{}).map(function(kv){return alabel(kv[0])+' '+kv[1];}).join(' · ')||'—';
  const actorRows=(a.actors||[]).map(function(x){
    const kind=x.actor_kind==='user'?'<span style="color:#2bd96a">账号</span>':'<span style="color:#7f8a96">访客</span>';
    return '<tr style="cursor:pointer" onclick="loadActivity(\\''+encodeURIComponent(x.actor_id)+'\\')"><td class="t">'+esc(x.actor_name)+'</td><td>'+kind+'</td><td class="c">'+x.actions+'</td><td style="color:#7f8a96;font-weight:400;white-space:nowrap">'+tshort(x.last_seen)+'</td></tr>';
  }).join('')||'<tr><td colspan=4 class="sub" style="padding:12px">暂无操作记录</td></tr>';
  const recRows=(a.recent||[]).map(function(x){
    const who=x.actor_kind==='user'?'<span style="color:#2bd96a">'+esc(x.actor_name)+'</span>':'<span style="color:#7f8a96">'+esc(x.actor_name)+'·'+esc((x.ip||'').slice(0,15))+'</span>';
    return '<tr><td style="white-space:nowrap;color:#7f8a96">'+tshort(x.ts)+'</td><td>'+who+'</td><td style="white-space:nowrap">'+esc(alabel(x.action))+'</td><td style="word-break:break-word;color:#d7dee7">'+esc(x.target||'')+'</td><td style="color:#7f8a96">'+(x.device==='mobile'?'📱':'💻')+'</td></tr>';
  }).join('')||'<tr><td colspan=5 class="sub" style="padding:12px">暂无</td></tr>';
  $('#act').innerHTML=
    '<div class="grid">'+
      card('总操作数',s.total,'amber','今日 '+(s.today||0))+
      card('活跃身份',s.actors,'up','登录账号 '+(s.users||0)+' · 匿名 '+((s.actors||0)-(s.users||0)))+
      card('登录账号操作',s.users,'blue','点下方账号看其全部操作')+
    '</div>'+
    '<div class="panel"><h2>🧭 操作流水 · 动作分布</h2><div class="sub">'+byAct+'</div></div>'+
    '<div class="panel"><h2>👥 按账号/访客（点击看其全部操作）'+(actor?' <button class="refresh" onclick="loadActivity(\\'\\')">↩ 全部</button>':'')+'</h2><table><thead><tr><th>身份</th><th>类型</th><th style="text-align:right">操作数</th><th>最近</th></tr></thead><tbody>'+actorRows+'</tbody></table></div>'+
    '<div class="panel"><h2>🕑 最近操作明细'+(actor?'（已筛选）':'')+'</h2><div style="max-height:520px;overflow:auto"><table><thead><tr><th style="width:140px">时间</th><th style="width:160px">谁</th><th style="width:110px">动作</th><th>对象</th><th style="width:34px"></th></tr></thead><tbody>'+recRows+'</tbody></table></div></div>';
}
function memTierBadge(t,label){ var c=t==='lifetime'?'#c4b5fd':(t==='premium'?'#ffb000':'#7f8a96'); return '<span style="color:'+c+';font-weight:700">'+esc(label)+'</span>'; }
function memRem(t,dl){ if(t==='lifetime') return '<span style="color:#c4b5fd;font-weight:700">永久</span>'; if(t!=='premium'||dl==null) return '<span class="sub">—</span>'; var c=dl<=7?'#ff5a52':(dl<=30?'#ffb000':'#2bd96a'); return '<span style="color:'+c+';font-weight:700;white-space:nowrap">'+(dl<=0?'今天到期':(dl+' 天'))+'</span>'; }
async function grantMember(){
  var u=($('#gm-user').value||'').trim(); var days=parseInt($('#gm-days').value||'0',10)||0; var paid=$('#gm-paid').checked; var res=$('#gm-result');
  if(!u){ res.style.color='#ff5a52'; res.textContent='请输入账号'; return; }
  res.style.color='#7f8a96'; res.textContent='处理中…';
  try{
    var r=await fetch('/api/admin/grant-membership',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,days:days,paid:paid,token:token})});
    var d=await r.json();
    if(!r.ok||!d.ok){ res.style.color='#ff5a52'; res.textContent='失败：'+((d&&d.detail)||r.status); return; }
    var m=d.membership||{}; var exp=(m.expires_at||'').slice(0,10);
    res.style.color='#2bd96a'; res.textContent='✓ '+u+' → '+(m.tier==='lifetime'?'永久':((m.tier_label||m.tier||'')+(m.days_left!=null?(' '+m.days_left+'天'):'')))+(exp?(' 到期 '+exp):'');
    loadMembers();
  }catch(e){ res.style.color='#ff5a52'; res.textContent='出错：'+e.message; }
}
async function loadMembers(){
  let d;
  try{ const r=await fetch('/api/metrics/members?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); d=await r.json(); }
  catch(e){ $('#members').innerHTML='<div class="panel"><h2>👑 会员</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const s=d.summary||{}; const ms=d.members||[];
  setPill('pill-expire', (s.expiring_7d>0?'warn':'ok'), (s.expiring_7d||0), '7 天内到期会员');
  // 回填第一屏 Hero KPI 的会员三件套
  kset('kpi-paid', (s.paid||0), '尊享 '+(s.premium||0)+' · 永久 '+(s.lifetime||0)+' · 体验 '+(s.trial||0), 'amber');
  kset('kpi-active', (s.active_today||0), '近7日 '+(s.active_7d||0)+' · 今日操作 '+(s.ops_today||0), 'up');
  kset('kpi-expire', (s.expiring_7d||0), '30天内共 '+(s.expiring_30d||0)+' 人 · 及时私信续费', ((s.expiring_7d||0)>0?'bad':'up'));
  const rows = ms.map(function(m){
    const phone = m.phone? '<span style="color:#6ab0ff;font-family:monospace">'+esc(m.phone)+'</span>' : '<span class="sub">—</span>';
    const exp = m.tier==='lifetime'?'永久':((m.expires_at||'').slice(0,10)||'—');
    return '<tr><td style="white-space:nowrap"><b>'+esc(m.name)+'</b><div style="font-size:11px;margin-top:2px">'+phone+'</div></td>'+
      '<td>'+memTierBadge(m.tier,m.tier_label)+'</td>'+
      '<td style="white-space:nowrap;color:#7f8a96">'+esc(exp)+'</td>'+
      '<td>'+memRem(m.tier,m.days_left)+'</td>'+
      '<td class="c">'+(m.ops_today||0)+'</td>'+
      '<td class="c" style="color:#6ab0ff">'+(m.ops_7d||0)+'</td>'+
      '<td style="color:#7f8a96;white-space:nowrap">'+(m.last_seen?tshort(m.last_seen):'—')+'</td></tr>';
  }).join('') || '<tr><td colspan=7 class="sub" style="padding:12px">暂无会员</td></tr>';
  const soon = ms.filter(function(m){ return m.tier==='premium' && m.days_left!=null && m.days_left<=7; });
  const alertRows = soon.length? soon.map(function(m){
    return '<tr><td style="white-space:nowrap"><b>'+esc(m.name)+'</b></td><td style="color:#6ab0ff;font-family:monospace">'+esc(m.phone||'—')+'</td><td class="c">'+memRem('premium',m.days_left)+'</td></tr>';
  }).join('') : '<tr><td colspan=3 class="sub" style="padding:12px">7 天内无到期会员 👍</td></tr>';
  $('#members').innerHTML=
    '<div class="panel"><h2>➕ 开通 / 续期会员</h2>'+
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'+
        '<input id="gm-user" placeholder="账号（用户名/邮箱）" style="flex:1;min-width:180px;padding:7px 10px;background:#0f141b;border:1px solid #263041;border-radius:6px;color:#e6ebf2;font-family:inherit">'+
        '<input id="gm-days" type="number" value="90" style="width:84px;padding:7px 10px;background:#0f141b;border:1px solid #263041;border-radius:6px;color:#e6ebf2;font-family:inherit"> 天'+
        '<label style="display:flex;align-items:center;gap:4px;color:#9fb0c0;font-size:13px"><input id="gm-paid" type="checkbox" checked> 计为付费</label>'+
        '<button class="refresh" onclick="grantMember()">开通 / 续期</button>'+
        '<span id="gm-result" class="sub"></span>'+
      '</div>'+
      '<div class="sub" style="margin-top:7px">月=30 · 季=90 · 半年=180 · 年=365 · <b>累加顺延</b>不缩短已有会员；取消会员填 0；赠送/补偿取消勾选「计为付费」</div>'+
    '</div>'+
    '<div class="grid">'+
      card('付费会员',s.paid,'amber','尊享 '+(s.premium||0)+' · 永久 '+(s.lifetime||0))+
      card('今日活跃会员',s.active_today,'up','近7日 '+(s.active_7d||0))+
      card('7 天内到期',s.expiring_7d,(s.expiring_7d>0?'amber':'up'),'30 天内共 '+(s.expiring_30d||0)+' 人')+
      card('会员今日操作',s.ops_today,'blue','体验用户 '+(s.trial||0)+' 人')+
    '</div>'+
    '<div class="panel"><h2>👑 会员明细 · 到期 / 操作量（按最快到期排序）</h2>'+
      '<div style="max-height:520px;overflow:auto"><table><thead><tr><th>会员 / 手机号</th><th>等级</th><th>到期日</th><th>剩余</th><th style="text-align:right">今日操作</th><th style="text-align:right">7日操作</th><th>最近活跃</th></tr></thead><tbody>'+rows+'</tbody></table></div></div>'+
    '<div class="panel"><h2>🔔 续费预警 · 7 天内到期</h2><table><thead><tr><th>会员</th><th>手机号</th><th style="text-align:right">剩余</th></tr></thead><tbody>'+alertRows+'</tbody></table></div>';
}
async function loadReferrals(){
  let d;
  try{ const r=await fetch('/api/metrics/referrals?token='+encodeURIComponent(token)); if(!r.ok) throw new Error(r.status); d=await r.json(); }
  catch(e){ $('#referrals').innerHTML='<div class="panel"><h2>🎁 邀请活动</h2><div class="sub">读取失败('+e.message+')</div></div>'; return; }
  const pend=d.pending||[]; const recent=d.recent||[];
  setPill('pill-redeem', ((d.pending_count||0)>0?'warn':'ok'), (d.pending_count||0), '待人工兑换');
  const stRow=function(r){ var c=r.status==='pending'?'#ffb000':(r.status==='granted'?'#2bd96a':'#7f8a96'); return '<tr><td><b>'+esc(r.inviter)+'</b></td><td>'+esc(r.card)+'</td><td class="c">'+(r.days||0)+'天</td><td style="color:'+c+';font-weight:700">'+esc(r.status)+'</td><td style="color:#7f8a96">'+esc(r.channel||'')+'</td><td style="color:#7f8a96;white-space:nowrap">'+(r.created_at?tshort(r.created_at):'')+'</td></tr>'; };
  const pendRows = pend.length? pend.map(stRow).join('') : '<tr><td colspan=6 class="sub" style="padding:12px">无待人工兑换 👍</td></tr>';
  const recRows = recent.length? recent.map(stRow).join('') : '<tr><td colspan=6 class="sub" style="padding:12px">暂无兑换记录</td></tr>';
  $('#referrals').innerHTML=
    '<div class="grid">'+
      card('付费会员',d.paid_members||0,'amber','会员来源=付费')+
      card('待人工兑换',d.pending_count||0,(d.pending_count>0?'amber':'up'),'自助受阻转人工')+
      card('已发奖天数',d.granted_days_total||0,'blue','累计发放会员天数')+
    '</div>'+
    '<div class="panel"><h2>🎁 邀请兑换 · 待人工复核'+(pend.length?'（'+pend.length+'）':'')+'</h2><table><thead><tr><th>邀请人</th><th>卡</th><th>天数</th><th>状态</th><th>渠道</th><th>时间</th></tr></thead><tbody>'+pendRows+'</tbody></table></div>'+
    '<div class="panel"><h2>🧾 最近兑换台账</h2><div style="max-height:420px;overflow:auto"><table><thead><tr><th>邀请人</th><th>卡</th><th>天数</th><th>状态</th><th>渠道</th><th>时间</th></tr></thead><tbody>'+recRows+'</tbody></table></div></div>';
}
// 点导航/待办跳到折叠抽屉时自动展开它（否则锚点只滚动、不打开）
function _openHashTarget(){ try{ var el=document.querySelector(location.hash); if(el&&el.tagName==='SUMMARY'){ var d=el.closest('details'); if(d)d.open=true; setTimeout(function(){el.scrollIntoView();},30); } }catch(e){} }
window.addEventListener('hashchange', _openHashTarget);
load();
</script></body></html>"""
from .research_wire import (
    list_research_wire,
    fetch_research_wire_online,
    probe_zsxq,
    save_zsxq_override,
    clear_zsxq_override,
    load_zsxq_override,
    parse_curl_cookie,
    auth_payload as zsxq_auth_payload,
)
from urllib.parse import quote, urlparse
from .research_workbench import (
    WORKBENCH_DIR,
    proxy_research_workbench,
    stop_research_workbench,
    warm_research_workbench,
)
from .shareholder_change_skill import (
    detect_shareholder_change_request,
    format_shareholder_change_skill_response,
    interpret_shareholder_change,
    scan_shareholder_changes,
)
from .schemas import (
    AgentBriefRequest,
    AgentEngineInfo,
    AgentEngineListResponse,
    AgentToolInfo,
    AgentToolListResponse,
    AgentRuntimeHealthResponse,
    DataQuality,
    ResearchReportItem,
    ResearchReportSearchResponse,
    ResearchWireItem,
    ResearchWireResponse,
    ResearchVisionAnalyzeRequest,
    NewsAnalyzeRequest,
    ResearchVisionAnalysisResponse,
    CapabilityListResponse,
    CnEarningsDiagnosisRequest,
    CnEarningsDiagnosisResponse,
    CnEarningsRecordDetailRequest,
    CnEarningsRecordDetailResponse,
    CnEarningsScanRequest,
    CnEarningsScanResponse,
    CorridorRiskRequest,
    CustomsTradeAnalysisRequest,
    DataSourceCreateRequest,
    DataSourceItemInterpretRequest,
    DataSourceItemInterpretResponse,
    DataSourceItemListResponse,
    DataSourceItemRecord,
    DataSourceItemUpdateRequest,
    DataSourceKeywordCrawlRequest,
    DataSourceKeywordCrawlResponse,
    DataSourceListResponse,
    DataSourceModuleRefCreateRequest,
    DataSourceModuleRefListResponse,
    DataSourceModuleRefRecord,
    DataSourceRecord,
    DataSourceSyncRequest,
    DataSourceSyncResponse,
    DataSourceCorpusStats,
    DataSourceTagListResponse,
    DulusMemoryCreateRequest,
    DulusMemoryListResponse,
    DulusMemoryRecord,
    DulusRoundtableRequest,
    DulusRoundtableResponse,
    DulusRuntimeStatusResponse,
    DulusToolRecord,
    DulusWebBridgeInspectRequest,
    DulusWebBridgeInspectResponse,
    EarningsCalendarResponse,
    FinGptTaskResponse,
    ForecastRequest,
    FileExtractionResponse,
    GeneralChatRequest,
    GeneralChatResponse,
    InvestmentTaskCreateRequest,
    InvestmentTaskListResponse,
    InvestmentTaskRecord,
    MarketQuoteListResponse,
    MarketDataLayerStatusResponse,
    MarketSymbolSearchResponse,
    AShareStructuredDataResponse,
    MajorEventScanRequest,
    MajorEventScanResponse,
    McpCapabilityListResponse,
    McpDiscoverResponse,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerRecord,
    McpToolCallRequest,
    McpToolCallResponse,
    NewsSummaryRequest,
    OfficialNewsResponse,
    PeopleSpotlightResponse,
    PersonDigestResponse,
    PersonProfile,
    ModelConfigRequest,
    ModelConfigResponse,
    MultiMarketDecisionRequest,
    MultiMarketDecisionResponse,
    OptionsAiAnalysisRequest,
    OptionsAiAnalysisResponse,
    OptionsSignalResponse,
    OrchestratorChatRequest,
    OrchestratorChatResponse,
    PremarketOpportunityResponse,
    ProfessionalEvalRunRequest,
    ProfessionalEvalRunResponse,
    ProfessionalMetricListResponse,
    ProfessionalRagQueryRequest,
    ProfessionalRagQueryResponse,
    ProfessionalReportAnalysisRequest,
    ProfessionalReportAnalysisResponse,
    ProfessionalReportChunkListResponse,
    ProfessionalReportIngestRequest,
    ProfessionalReportListResponse,
    ProfessionalReportRecord,
    ProfessionalReportUrlIngestRequest,
    ProfessionalWorkbenchFileIngestRequest,
    RagQueryRequest,
    RealtimeMessageCreateRequest,
    RealtimeMessageListResponse,
    RecallDeliveryLogResponse,
    RecallDeliveryResult,
    RecallMetricsResponse,
    RecallSubscriptionCreateRequest,
    RecallSubscriptionListResponse,
    RecallSubscriptionRecord,
    ShareSnapshotCreateRequest,
    ShareSnapshotRecord,
    RealtimeMessageRecord,
    ReportAnalysisRequest,
    SentimentRequest,
    SentimentResponse,
    attach_data_quality,
    classify_data_quality,
    ShareholderChangeInterpretRequest,
    ShareholderChangeInterpretResponse,
    ShareholderChangeScanRequest,
    ShareholderChangeScanResponse,
    StockAnalysisRequest,
    StockAnalysisResponse,
    StockCheckRequest,
    StockCheckResponse,
    StockCheckStep,
    BriefingResponse,
    MarketQuote,
    StockCompareItem,
    StockCompareResponse,
    StockScreenCriterion,
    StockScreenMatch,
    StockScreenResponse,
    MacroReviewResponse,
    PortfolioReviewResponse,
    SystemReadinessCheck,
    TearSheetResponse,
    SystemReadinessResponse,
    GreeksRequest,
    GreeksResponse,
    PnlRecord,
    PnlSummaryResponse,
    PositionCloseRequest,
    PositionCreateRequest,
    PositionListResponse,
    PositionRecord,
    PositionRiskMetrics,
    PositionUpdateRequest,
    RiskAlert,
    RiskLimitRecord,
    RiskLimitUpdateRequest,
    RiskSummaryResponse,
    BacktestCreateRequest,
    BacktestListResponse,
    BacktestMetricsRequest,
    BacktestMetricsResponse,
    BacktestRecord,
    MarketDashboardResponse,
    DashboardAnalysisResponse,
    ModuleContextChatRequest,
    CrossModuleResearchRequest,
    CrossModuleResearchResponse,
)

load_dotenv()


def _safe_workbench_file_path(out: str, filename: str) -> Path:
    root = WORKBENCH_DIR.resolve()
    base = (root / (out or "downloads/海外投行报告")).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="抓取目录不在研报工作台内。") from exc

    file_path = (base / filename).resolve()
    try:
        file_path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="文件路径越界。") from exc

    if file_path.name in {"manifest.json", "files.csv"} or file_path.name.endswith(".part"):
        raise HTTPException(status_code=400, detail="该文件不是可入库研报。")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="抓取文件不存在。")
    return file_path


def _allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()] or ["*"]
    if "*" in origins:
        return origins
    local_origins = [
        f"http://{host}:{port}"
        for host in ("localhost", "127.0.0.1")
        for port in range(3000, 3016)
    ]
    return list(dict.fromkeys([*origins, *local_origins]))

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_data_source_egress()  # 数据源域名绕过出网代理（封锁环境下仍能直连取数）
    init_auth()  # 建认证表（统一存储层）+ 按 env 预置管理员
    init_task_db()
    init_data_source_db()
    init_professional_research_db()
    init_realtime_message_db()
    init_recall_subscription_db()
    from .weixin_bind import init_weixin_bind_db as _init_weixin_bind_db
    _init_weixin_bind_db()  # 微信 iLink 绑定表（账号↔bot 多租户「扫码即问」）
    # 微信 iLink 渠道（扫码即问 + 准推送）——灰色地基/有封号风险，默认关；DEEPFOCUS_WEIXIN_CHANNEL=1 显式开
    global _WEIXIN_MGR
    if os.getenv("DEEPFOCUS_WEIXIN_CHANNEL", "0") == "1":
        from .weixin_channel import WeixinChannelManager
        _WEIXIN_MGR = WeixinChannelManager(agent_fn=make_weixin_orchestrator_agent_fn())
        _WEIXIN_MGR.start()
        print("[weixin] iLink 渠道已启动（多租户扫码即问）")
    init_share_snapshot_db()
    init_data_store()  # 持久化数据层：速判卡/行情写穿落库，历史积累 + 读缓存
    init_metrics_db()  # 站点指标：页面访问 / 研报下载计数
    # 新信号落库广播后，扇出到离线召回订阅（邮件 / Web Push）。
    register_post_message_hook(lambda message: dispatch_recall(message))
    init_mcp_db()
    init_risk_db()
    init_backtest_db()
    init_dulus_runtime_db()
    await warm_research_workbench()
    await start_agent_worker()
    # DAO 财经事件桥接：后台轮询 DAO 事件 API → 灌进实时消息流（金融终端用）
    dao_bridge_task = asyncio.create_task(run_dao_bridge())
    # 缓存预热：定时 force 刷新宏观看板等重外部源，请求只读暖缓存（消除冷取延迟）
    from .cache_warmer import run_cache_warmer

    cache_warmer_task = asyncio.create_task(run_cache_warmer())
    # 研报预解读：后台把最新研报逐篇 AI 解读并缓存，用户点开即秒回
    research_prewarm_task = asyncio.create_task(run_research_prewarm())
    # 研报列表缓存保活：让研报面板每次加载都秒开
    wire_refresher_task = asyncio.create_task(run_wire_refresher())
    # 文章 AI 预解读：拉取到的文章后台预先解读并缓存
    news_prewarm_task = asyncio.create_task(run_news_prewarm())
    # AI 头条评选：华尔街视角挑真正重要的头条
    headline_task = asyncio.create_task(run_headline_picker())
    # 研报登录态健康监测：cookie 失效即看板红灯 + 邮件告警
    zsxq_health_task = asyncio.create_task(run_zsxq_health())
    # 微信桥接(gewechat)登录态健康监测：每 5min 探活，掉线即邮件告警提醒重新扫码
    wechat_health_task = asyncio.create_task(run_wechat_health())
    # AI 解读缓存：每日清理过期(默认 >90 天)条目，防长期累积
    cache_pruner_task = asyncio.create_task(run_cache_pruner())
    # A股收盘复盘：每个交易日 15:35 生成「大盘+板块+个股 × 我们提前发现的资讯」复盘
    ashare_review_task = asyncio.create_task(run_ashare_review())
    # 增长分析师：每日 16:20 自动计算 KPI（用户/留存/日活/付费转化）+ AI 改进建议 → 运营看板
    growth_analytics.init_growth_db()
    growth_analyst_task = asyncio.create_task(run_growth_analyst())
    from .checkin import init_checkin_db
    init_checkin_db()  # 连续看复盘签到表
    ai_fund.init_ai_fund_db()  # A股 AI 模拟盘（虚拟基金）账户表
    # AI 模拟盘交易员：A股交易时段内每 30min 跑一轮多因子决策（自动模拟买卖），展示给大家看
    ai_fund_task = asyncio.create_task(run_ai_fund_trader())
    from .partner_api import init_partner_db
    init_partner_db()  # 合作方/开发者 API（自有内容对外）
    # T+1 召回：每日 10:30 给「注册 24~72h 未回访且留邮箱」的新用户发带当日复盘内容的召回邮件
    t1_recall_task = asyncio.create_task(run_t1_recall())
    # 到期转化：每日 11:00 给「会员 48h 内到期、非付费、留邮箱」的用户发续费提醒（最高意向时刻）
    expiry_reminder_task = asyncio.create_task(run_expiry_reminder())
    # 合作方 API 续费/对账告警：每日 09:30 给管理员汇总近配额/近到期/待收款
    partner_alert_task = asyncio.create_task(run_partner_billing_alerts())
    yield
    # 优雅关停但不无限等：后台任务可能卡在不可取消的 to_thread(渲染)/长 LLM 调用里，
    # 给一个总超时，超时就直接放手让进程退出（避免每次重启都等满 systemd 停服超时）。
    _bg_tasks = (dao_bridge_task, cache_warmer_task, research_prewarm_task,
                 wire_refresher_task, news_prewarm_task, headline_task, zsxq_health_task, wechat_health_task, cache_pruner_task,
                 ashare_review_task, growth_analyst_task, t1_recall_task, expiry_reminder_task, partner_alert_task,
                 ai_fund_task)
    for _task in _bg_tasks:
        _task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*_bg_tasks, return_exceptions=True), timeout=4.0)
    except (asyncio.TimeoutError, BaseException):
        pass
    for _closer in (stop_agent_worker, stop_research_workbench):
        try:
            await asyncio.wait_for(_closer(), timeout=4.0)
        except (asyncio.TimeoutError, BaseException):
            pass


app = FastAPI(
    title="DeepFocus AI API",
    description="Cloud-model research API for the DeepFocus frontend.",
    version="0.1.0",
    lifespan=lifespan,
)

# 先加鉴权中间件、后加 CORS，使 CORS 处于最外层——
# 这样鉴权返回的 401/403 也会带上 CORS 头，浏览器能正确读到状态码而非报跨域。
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

llm = CloudResearchLLM()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "deepfocus-ai-api",
        "provider": llm.provider_name,
        "model": llm.model,
    }


async def _verify_turnstile(token: str, ip: str) -> bool:
    """Cloudflare Turnstile 校验。未启用→放行；启用但 CF 不可达(可能被墙)→放行(fail-open，保真人)。"""
    if not turnstile_enabled():
        return True
    if not (token or "").strip():
        return turnstile_soft()  # 无票据：软模式放行(防大陆误伤)，强制模式拒
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": turnstile_secret(), "response": token, "remoteip": ip or ""},
            )
        return bool((r.json() or {}).get("success"))
    except Exception:  # noqa: BLE001 - CF 不可达(被墙/超时)→放行，避免锁死真人；其余反刷防线仍在
        return True


@app.get("/api/auth/captcha")
async def auth_captcha_config() -> dict[str, Any]:
    """人机校验配置（公开）：前端据此决定是否渲染 Turnstile 框。"""
    return {"enabled": turnstile_enabled(), "provider": "turnstile", "sitekey": turnstile_sitekey()}


@app.post("/api/auth/register", response_model=TokenResponse)
async def auth_register(payload: RegisterRequest, request: Request) -> TokenResponse:
    # 已有用户后是否允许自助注册由 env 控制；机构部署可关掉只让管理员开账号。
    if not self_register_enabled() and count_users() > 0:
        raise HTTPException(status_code=403, detail="自助注册已关闭，请联系管理员开通账号")
    # 反刷号：蜜罐字段（真人前端隐藏置空，机器人常填）→ 静默判失败
    if (payload.website or "").strip():
        raise HTTPException(status_code=400, detail="注册失败，请稍后再试")
    # 反刷号：同一 IP 24h 注册数封顶（默认 5，env 可调），挡同 IP 批量开号
    reg_ip = _client_ip(request)
    if count_recent_ip_registrations(reg_ip, 24) >= reg_ip_daily_max():
        raise HTTPException(status_code=429, detail="该网络今日注册过于频繁，请明天再试或联系管理员")
    # 反刷号：拦截一次性/临时邮箱
    if is_disposable_email(payload.email):
        raise HTTPException(status_code=422, detail="请使用常用邮箱注册，暂不支持临时邮箱")
    # 反刷号：Cloudflare Turnstile 人机校验（仅在配了 key 时启用；CF 不可达自动放行保真人）
    if not await _verify_turnstile(payload.turnstile_token or "", reg_ip):
        raise HTTPException(status_code=400, detail="人机校验未通过，请重试")
    # 用户名禁用 HTML/控制字符（纵深防御：杜绝看板/页面渲染时的注入面）
    if re.search(r"""[<>"'&]""", payload.username or "") or any(ord(ch) < 32 for ch in (payload.username or "")):
        raise HTTPException(status_code=422, detail="用户名不能包含 < > \" ' & 等特殊字符")
    # 邮箱/手机号收集但不强制：填了才校验格式；留空则跳过（仅用户名+密码即可注册）。
    if payload.email and payload.email.strip() and not is_valid_email(payload.email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    if payload.phone and payload.phone.strip() and not is_valid_phone(payload.phone):
        raise HTTPException(status_code=422, detail="手机号格式不正确")
    # 手机号、邮箱至少填一项（账号找回/触达的最低保障）。
    if not (payload.email and payload.email.strip()) and not (payload.phone and payload.phone.strip()):
        raise HTTPException(status_code=422, detail="请至少填写手机号或邮箱其一")
    try:
        user = create_user(
            payload.email, payload.username, payload.password, phone=payload.phone,
            invite_code_used=payload.invite_code, registered_ip=_client_ip(request),
        )
    except UserExistsError:
        raise HTTPException(status_code=409, detail="邮箱或用户名已存在")
    try:  # 注册落一条流水（含是否由邀请码带来）
        metrics_log_activity(actor_kind="user", actor_id=f"u:{user.id}",
                             actor_name=str(user.username or user.email or user.id),
                             action="login", target=("注册" + (f"·邀请码{payload.invite_code.strip().upper()}" if (payload.invite_code or '').strip() else "")),
                             ip=_client_ip(request))
    except Exception:  # noqa: BLE001
        pass
    sid = rotate_session(user.id)  # 单设备登录：注册即建立会话标识
    return TokenResponse(access_token=create_access_token(user, sid=sid), user=user)


@app.get("/api/auth/invite", response_model=InviteOverview)
async def auth_invite(request: Request) -> InviteOverview:
    """我的拉新概览：专属邀请码 + 已邀请人数 + 最近被邀请者（需登录）。"""
    claims = require_current_user(request)
    overview = get_invite_overview(str(claims.get("sub", "")))
    if overview is None:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return overview


@app.get("/api/me/referral")
async def me_referral(request: Request) -> dict[str, Any]:
    """我的邀请奖励：进度 + 卡包(可兑换) + 被邀请人状态（需登录）。"""
    claims = require_current_user(request)
    data = referral.overview(str(claims.get("sub", "")))
    if data is None:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return data


@app.get("/api/referral/leaderboard")
async def referral_leaderboard(limit: int = 20) -> dict[str, Any]:
    """邀请排行榜（脱敏，竞争驱动）。免登录可看。"""
    return referral.leaderboard(limit=max(3, min(int(limit or 20), 50)))


@app.post("/api/admin/campaign/settle")
async def admin_campaign_settle(request: Request, token: str = "", confirm: bool = False) -> dict[str, Any]:
    """结算冲榜赛（管理端，需令牌）。默认仅预览前十名+防刷标记；confirm=true 才真正发奖+私信通知（幂等防重发）。
    例：curl -X POST '.../api/admin/campaign/settle?token=XXX'           # 预览
        curl -X POST '.../api/admin/campaign/settle?token=XXX&confirm=true'  # 确认发奖"""
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    return referral.settle_campaign(confirm=confirm)


@app.get("/api/referral/campaign")
async def referral_campaign(request: Request) -> dict[str, Any]:
    """限时邀请冲榜赛：榜单 + 奖励档 + 倒计时；登录则附「我的排名/当前可得奖/反超所需」。免登录可看。"""
    claims = current_claims(request)
    uid = str(claims.get("sub", "")) if claims else None
    return referral.campaign(uid)


@app.post("/api/me/referral/redeem")
async def me_referral_redeem(request: Request) -> dict[str, Any]:
    """兑换一张邀请奖励卡（需登录）：干净的自助秒兑顺延会员，可疑的转人工。body: {card_type: month|quarter|year}"""
    claims = require_current_user(request)
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    result = referral.redeem(str(claims.get("sub", "")), str(body.get("card_type") or ""))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "兑换失败")
    return result


@app.get("/api/metrics/referrals")
async def metrics_referrals(request: Request, token: str = "") -> dict[str, Any]:
    """运营看板·邀请活动数据（需 metrics 令牌：?token= 或 X-Metrics-Token）。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    return referral.admin_referrals()


@app.post("/api/auth/login", response_model=TokenResponse)
async def auth_login(payload: LoginRequest, request: Request) -> TokenResponse:
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    try:  # 登录成功记一条操作流水（精确到账号）
        ua = request.headers.get("user-agent") or ""
        metrics_log_activity(
            actor_kind="user", actor_id=f"u:{user.id}",
            actor_name=str(user.username or user.email or user.id), action="login",
            target="", ip=_client_ip(request), device=("mobile" if _MOBILE_UA_RE.search(ua) else "pc"),
        )
    except Exception:  # noqa: BLE001
        pass
    sid = rotate_session(user.id)  # 单设备登录：本次登录改写会话标识 → 其他端旧 token 失效被挤下线
    return TokenResponse(access_token=create_access_token(user, sid=sid), user=user)


@app.get("/api/auth/me", response_model=AuthUserOut)
async def auth_me(request: Request) -> AuthUserOut:
    claims = require_current_user(request)
    user = get_user_out_by_id(str(claims.get("sub", "")))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


@app.get("/api/auth/users", response_model=UserListResponse)
async def auth_list_users(request: Request) -> UserListResponse:
    require_admin(request)  # handler 级守卫：即便旁路态也只放行管理员
    return UserListResponse(users=list_users())


def _admin_token_ok(request: Request, token: str) -> bool:
    """管理令牌校验：专用 DEEPFOCUS_MEMBERSHIP_TOKEN 或看板 DEEPFOCUS_METRICS_TOKEN，任一匹配即放行。"""
    mem = (os.getenv("DEEPFOCUS_MEMBERSHIP_TOKEN") or "").strip()
    met = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Admin-Token") or request.headers.get("X-Metrics-Token") or "").strip()
    if not provided:
        return False
    return (bool(mem) and provided == mem) or (bool(met) and provided == met)


@app.post("/api/admin/membership")
async def admin_set_membership(
    request: Request, username: str = "", tier: str = "premium", days: int = 0, token: str = "", paid: bool = False,
) -> dict[str, Any]:
    """修改用户会员级别（管理端，需管理令牌）。SET 语义、直接设定不叠加。query 参数或 JSON body 均可（中文用户名走 body 免编码）：
    - tier=premium & days=400 → 尊享会员，400 天后到期
    - tier=lifetime           → 永久会员
    - tier=trial              → 体验期（取消会员）
    例：curl -X POST .../api/admin/membership -H 'Content-Type: application/json' -d '{"username":"响哥哥","tier":"premium","days":400,"token":"XXX"}'"""
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 无 body 时按 query 参数走
        body = {}
    username = (username or str(body.get("username") or "")).strip()
    tier = str(body.get("tier") or tier or "premium").strip()
    if body.get("days") is not None:
        try:
            days = int(body["days"])
        except (TypeError, ValueError):
            pass
    token = token or str(body.get("token") or "")
    if body.get("paid") is not None:
        paid = bool(body["paid"])
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    if not username:
        raise HTTPException(status_code=422, detail="缺少 username")
    result = set_membership(username, tier, days, source=("paid" if paid else "admin"))
    if result is None:
        raise HTTPException(status_code=404, detail=f"用户不存在：{username}")
    return {"ok": True, "username": username, "tier": tier, "membership": result}


@app.post("/api/admin/grant-membership")
async def admin_grant_membership(
    request: Request, username: str = "", days: int = 0, token: str = "", permanent: bool = False, paid: bool = False, until: str = "",
) -> dict[str, Any]:
    """给某用户开/续/取消会员（管理端，需 metrics 令牌）。query 参数或 JSON body 均可（中文用户名用 body 免编码）。
    days>0 → 开/续 N 天尊享会员；days<=0 → 取消会员(转体验期/非会员)；permanent=true → 永久会员；
    until=YYYY-MM-DD → 直接把到期日设为该天（精确到期日，覆盖原有，当天有效到 23:59）。
    例(body)：curl -X POST .../api/admin/grant-membership -H 'Content-Type: application/json'
              -d '{"username":"zheng.520","until":"2027-05-08","token":"XXX"}'"""
    body: dict = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 无 body 时按 query 参数走
        body = {}
    username = (username or str(body.get("username") or "")).strip()
    if body.get("days") is not None:
        try:
            days = int(body["days"])
        except (TypeError, ValueError):
            pass
    if body.get("permanent") is not None:
        permanent = bool(body["permanent"])
    if body.get("paid") is not None:
        paid = bool(body["paid"])
    until = (until or str(body.get("until") or "")).strip()
    token = token or str(body.get("token") or "")
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    if not username:
        raise HTTPException(status_code=422, detail="缺少 username")
    source = "paid" if paid else "admin"
    if until:  # 精确到期日：当天 23:59:59（UTC）有效
        try:
            dt = datetime.strptime(until, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="until 需为 YYYY-MM-DD 格式")
        result = set_membership_expiry(username, dt, source=source)
    else:
        result = grant_membership(username, days, permanent=permanent, source=source)
    if result is None:
        raise HTTPException(status_code=404, detail=f"用户不存在：{username}")
    return {"ok": True, "username": username, "membership": result}


# --------------------------------------------------------------------------- #
# 会员兑换码（卡密）：后台批量生成 → 用户自助兑换 → 自动开通（算付费，计入邀请奖励）
# --------------------------------------------------------------------------- #
@app.post("/api/membership/claim-trial")
async def membership_claim_trial(request: Request) -> dict[str, Any]:
    """自助领取「登录送 3 天体验会员」：每账号仅一次，原子防重复。"""
    claims = require_current_user(request)
    uid = str(claims.get("sub", ""))
    res = claim_trial(uid, days=3)
    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "claimed":
            raise HTTPException(status_code=400, detail="你已领取过体验会员")
        if reason == "already_member":
            raise HTTPException(status_code=400, detail="你已是会员，无需领取体验会员")
        raise HTTPException(status_code=400, detail="领取失败，请稍后再试")
    return {"ok": True, "days": res.get("days", 3), "membership": res.get("membership")}


@app.post("/api/checkin")
async def api_checkin(request: Request) -> dict[str, Any]:
    """连续看复盘签到：登录用户打开当日复盘即记一次（幂等）。返回连续/累计 + 新达成的里程碑奖励。"""
    claims = require_current_user(request)
    uid = str(claims.get("sub", ""))
    uname = str(claims.get("username") or claims.get("email") or uid)
    from .checkin import record_checkin
    return record_checkin(uid, uname)


@app.get("/api/checkin/me")
async def api_checkin_me(request: Request) -> dict[str, Any]:
    """当前用户签到状态：今天是否已签 / 连续 / 累计 / 距下一个里程碑还差几天。"""
    claims = require_current_user(request)
    uid = str(claims.get("sub", ""))
    from .checkin import checkin_status
    return checkin_status(uid)


# ===== iFinD 专业数据（同花顺）——目前只对白名单账号开放（默认 lx199710）=====
def _require_ifind_user(request: Request) -> dict:
    """登录 + 用户名在 iFinD 白名单内才放行；否则 403。返回 claims。"""
    from . import ifind_api
    claims = require_current_user(request)
    if str(claims.get("username") or "").strip().lower() not in ifind_api.allowed_usernames():
        raise HTTPException(status_code=403, detail="iFinD 专业数据暂未对你的账号开放")
    return claims


def ifind_enhance_enabled(request: Request) -> bool:
    """当前请求是否启用 iFinD 增强（灰度）。给查询型端点用——**绝不抛**，任何异常退化为不增强(fail-closed)。
    匿名/失效 token → optional_current_user 返回 None → 不增强，行为与现网完全一致。"""
    from . import ifind_api
    try:
        claims = optional_current_user(request)
        return ifind_api.user_allowed(str((claims or {}).get("username") or ""))
    except Exception:  # noqa: BLE001
        return False


@app.get("/api/ifind/status")
async def api_ifind_status(request: Request) -> dict[str, Any]:
    """iFinD 配置/鉴权自检（白名单账号）。"""
    _require_ifind_user(request)
    from . import ifind_api
    return ifind_api.access_token_status()


@app.get("/api/ifind/quote")
async def api_ifind_quote(request: Request, codes: str = "", indicators: str = "") -> dict[str, Any]:
    """A 股实时行情 + 基本面（同花顺 iFinD，白名单账号）。codes 逗号分隔，裸 6 位自动补后缀。"""
    _require_ifind_user(request)
    from . import ifind_api
    if not codes.strip():
        raise HTTPException(status_code=422, detail="请传 codes（如 600519 或 600519.SH，逗号分隔）")
    return await asyncio.to_thread(
        ifind_api.real_time_quote, codes, (indicators.strip() or ifind_api.DEFAULT_INDICATORS)
    )


@app.post("/api/membership/redeem")
async def membership_redeem(request: Request) -> dict[str, Any]:
    """登录用户用兑换码自助开通会员。"""
    claims = require_current_user(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="请输入兑换码")
    uid = str(claims.get("sub", ""))
    uname = str(claims.get("username") or claims.get("email") or uid)
    res = membership_codes.redeem(code, uid, uname)
    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "self_used":  # 同一账号重复兑换(多为连点)：会员已到账，给确认而非吓人的红叉
            mem = membership_of_username(uname)
            return {"ok": True, "tier": (mem or {}).get("tier", "premium"), "days": 0, "membership": mem,
                    "already": True, "message": "你已用此兑换码成功开通过，会员已到账，无需重复兑换。"}
        if reason == "trial_daily":  # 体验卡：每人每天限兑 1 张
            raise HTTPException(status_code=429, detail="今天已领取过体验卡啦，明天再来领一张～")
        msg = "兑换码不存在或无效" if reason == "not_found" else ("该兑换码已被使用" if reason == "used" else "兑换失败，请稍后再试")
        raise HTTPException(status_code=400, detail=msg)
    # 时长「累加顺延」而非替换：grant_membership 会从现有到期日往后加，不缩短已有会员。
    days = int(res.get("days") or 0)
    is_trial = res.get("kind") == "trial"
    if is_trial:
        # 体验卡(免费福利)的两条护栏：
        # ① 永久会员领体验卡 → 不动其会员（grant 会把永久哨兵重置成 7 天=降级），直接返回现状；
        # ② 已是付费会员 → 续期时保留 source=paid，免费体验卡不抹掉付费身份（否则邀请人丢失付费转化奖励）。
        cur = membership_of_username(uname) or {}
        if cur.get("tier") == "lifetime":
            mem = cur
        else:
            keep_paid = (membership_source_of(uname) == "paid")
            mem = grant_membership(uname, days, source=("paid" if keep_paid else "trial"))
    elif res["tier"] == "lifetime":
        mem = grant_membership(uname, 0, permanent=True, source="paid")
    else:
        mem = grant_membership(uname, days, source="paid")
    return {"ok": True, "tier": res["tier"], "days": days, "trial": is_trial, "membership": mem}


@app.post("/api/admin/codes/generate")
async def admin_codes_generate(
    request: Request, count: int = 10, tier: str = "premium", days: int = 30, note: str = "", token: str = "",
) -> dict[str, Any]:
    """批量生成会员兑换码（管理端，需令牌）。tier=premium&days=30 月卡 / days=90 季卡 / days=365 年卡 / tier=lifetime 永久。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    token = token or str(body.get("token") or "")
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    count = int(body.get("count", count) or count)
    tier = str(body.get("tier") or tier or "premium")
    days = int(body.get("days", days) if body.get("days") is not None else days)
    note = str(body.get("note") or note or "")
    codes = membership_codes.generate_codes(count, tier, days, note)
    return {"ok": True, "count": len(codes), "tier": tier, "days": days, "note": note, "codes": codes}


@app.get("/api/admin/codes")
async def admin_codes_list(request: Request, token: str = "", only_unused: bool = False) -> dict[str, Any]:
    """列出兑换码 + 统计（管理端=看板，需令牌）。"""
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    return {"stats": membership_codes.code_stats(), "codes": membership_codes.list_codes(only_unused=only_unused)}


# --------------------------------------------------------------------------- #
# 移动 App：版本检查（Capacitor 壳；内容随网站发布自动更新，此接口只管壳本身升级）
# --------------------------------------------------------------------------- #
@app.get("/api/app/version")
async def app_version_get() -> dict[str, Any]:
    """安卓壳最新版本信息（公开）。运维改环境变量即可推新版，无需改代码。"""
    return {
        "android": {
            "version_code": int(os.getenv("DEEPFOCUS_ANDROID_VERSION_CODE", "1")),
            "version_name": os.getenv("DEEPFOCUS_ANDROID_VERSION_NAME", "1.0"),
            "apk_url": os.getenv(
                "DEEPFOCUS_ANDROID_APK_URL", "https://daocaijing.com/downloads/deepfocus.apk"
            ),
            "notes": os.getenv("DEEPFOCUS_ANDROID_NOTES", ""),
        }
    }


# --------------------------------------------------------------------------- #
# 收款/购买：个人收款码方案（套餐价格 + 收款码图片；用户扫码付 → 私信管理员 → 看板核对开通）
# --------------------------------------------------------------------------- #
@app.get("/api/payment-config")
async def payment_config_get() -> dict[str, Any]:
    """购买页配置（公开）：套餐价格 + 是否已上传微信/支付宝收款码 + 说明。"""
    return payment_config.get_config()


@app.get("/api/payment-qr/{which}")
async def payment_qr_get(which: str):
    """收款码图片（公开）。which ∈ wechat | alipay。"""
    p = payment_config.qr_file(which)
    if p is None:
        raise HTTPException(status_code=404, detail="未设置该收款码")
    media = "image/png"  # 按真实字节判类型（收款码可能是 PNG 或 JPG）
    try:
        if p.read_bytes()[:3] == b"\xff\xd8\xff":
            media = "image/jpeg"
    except Exception:  # noqa: BLE001
        pass
    return FileResponse(str(p), media_type=media, headers={"Cache-Control": "no-cache"})


@app.post("/api/admin/payment-config")
async def admin_payment_config_set(request: Request) -> dict[str, Any]:
    """改套餐价格/说明/启用（管理端，需令牌）。body: {enabled, note, packages, token}。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not _admin_token_ok(request, str(body.get("token") or "")):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    return {"ok": True, "config": payment_config.set_config(body)}


@app.post("/api/admin/payment-qr")
async def admin_payment_qr_upload(request: Request) -> dict[str, Any]:
    """上传收款码图片（管理端，需令牌）。body JSON: {which:wechat|alipay, image:dataURL或base64, token}。
    用 base64+JSON 而非 multipart，规避前置 WAF/云盾对二进制文件上传的拦截。"""
    import base64 as _b64
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    token = str(body.get("token") or request.query_params.get("token") or "")
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    which = str(body.get("which") or request.query_params.get("which") or "wechat")
    img = str(body.get("image") or "").strip()
    if img.lower().startswith("data:") and "," in img:  # 去掉 data:image/png;base64, 前缀
        img = img.split(",", 1)[1]
    try:
        data = _b64.b64decode(img, validate=False)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="图片数据无效")
    if not data:
        raise HTTPException(status_code=422, detail="图片为空")
    if len(data) > 6 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大（>6MB）")
    if not payment_config.save_qr(which, data):
        raise HTTPException(status_code=400, detail="保存失败（which 仅支持 wechat/alipay 或 <provider>_<套餐key>）")
    return {"ok": True, "which": which}


# --------------------------------------------------------------------------- #
# 后台私信：登录用户 ↔ 管理员（用户端发信/拉会话；管理端=看板，令牌鉴权，收发回复）
# --------------------------------------------------------------------------- #
_SUPPORT_AUTOREPLY = os.getenv("DEEPFOCUS_SUPPORT_AUTOREPLY", "").strip() or (
    "【自动回复】您好！已收到您的留言，管理员会尽快人工回复您 👋\n\n"
    "几个自助小贴士：\n"
    "• 开通 / 查看会员：点右上角头像 →「会员中心」\n"
    "• 邀请好友得权益：头像 →「我的邀请码」，把专属链接发给朋友\n"
    "• 研报支持全市场在线检索；点任意研报 / 文章可一键 AI 解读；点行情里的个股可看它的相关快讯 / 文章 / 研报\n\n"
    "有任何问题继续在此留言即可，我们会尽快跟进。"
)


@app.post("/api/support/send")
async def support_send(request: Request) -> dict[str, Any]:
    """登录用户给管理员发一条私信；首次留言自动回复一条引导。"""
    claims = require_current_user(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    uid = str(claims.get("sub", ""))
    uname = str(claims.get("username") or claims.get("email") or uid)
    is_first = not support_store.get_thread(uid, limit=1)  # 发这条之前会话是否为空
    msg = support_store.add_message(uid, uname, "user", content)
    if msg is None:
        raise HTTPException(status_code=400, detail="发送失败")
    auto_reply = None
    if is_first and _SUPPORT_AUTOREPLY:  # 首次留言 → 自动引导回复（之后转人工）
        auto_reply = support_store.add_message(uid, uname, "admin", _SUPPORT_AUTOREPLY)
    return {"ok": True, "message": msg, "auto_reply": auto_reply}


@app.get("/api/support/thread")
async def support_thread(request: Request) -> dict[str, Any]:
    """登录用户拉自己的会话（顺带把管理员回复标为已读）。"""
    claims = require_current_user(request)
    uid = str(claims.get("sub", ""))
    msgs = support_store.get_thread(uid)
    support_store.mark_read(uid, "user")
    return {"messages": msgs}


@app.get("/api/support/unread")
async def support_unread(request: Request) -> dict[str, Any]:
    """登录用户未读数（管理员回复但没看的）→ 头像红点。"""
    claims = require_current_user(request)
    return {"unread": support_store.user_unread(str(claims.get("sub", "")))}


@app.get("/api/admin/support/unread-count")
async def admin_support_unread_count(request: Request) -> dict[str, Any]:
    """管理员（JWT）：用户发来的未读私信总数 → 终端主页提醒。非管理员返回 0（不报错）。"""
    claims = require_current_user(request)
    is_admin = str(claims.get("role") or "").strip().lower() == "admin"
    owners = {u.strip().lower() for u in (os.getenv("DEEPFOCUS_METRICS_OWNERS") or "").split(",") if u.strip()}
    uname = str(claims.get("username") or "").strip().lower()
    if not (is_admin or (owners and uname in owners)):
        return {"unread": 0}
    return {"unread": support_store.admin_unread_total()}


@app.get("/api/admin/support/threads")
async def admin_support_threads(request: Request, token: str = "") -> dict[str, Any]:
    """管理端（看板）：所有私信会话概览 + 总未读。"""
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    return {"threads": support_store.list_threads(), "unread_total": support_store.admin_unread_total()}


@app.get("/api/admin/support/thread")
async def admin_support_thread(request: Request, user_id: str = "", token: str = "") -> dict[str, Any]:
    """管理端：看某用户的完整会话（顺带标为管理员已读）。"""
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    uid = (user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=422, detail="缺少 user_id")
    msgs = support_store.get_thread(uid)
    support_store.mark_read(uid, "admin")
    return {"messages": msgs}


@app.post("/api/admin/support/reply")
async def admin_support_reply(request: Request) -> dict[str, Any]:
    """管理端：回复某用户。body: {user_id, content, token}。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    token = str(body.get("token") or request.query_params.get("token") or "")
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    uid = str(body.get("user_id") or "").strip()
    content = str(body.get("content") or "").strip()
    if not uid or not content:
        raise HTTPException(status_code=422, detail="缺少 user_id 或 content")
    # 取该用户最近用户名（会话里有），否则用 uid
    threads = {t["user_id"]: t["username"] for t in support_store.list_threads()}
    uname = threads.get(uid, uid)
    msg = support_store.add_message(uid, uname, "admin", content)
    if msg is None:
        raise HTTPException(status_code=400, detail="回复失败")
    return {"ok": True, "message": msg}


# --------------------------------------------------------------------------- #
# 账号自选股（登录态）：未登录走前端 localStorage，登录后绑定账号、跨设备同步
# --------------------------------------------------------------------------- #
from pydantic import BaseModel as _BaseModel  # noqa: E402


class WatchlistPayload(_BaseModel):
    symbols: list[str] = []
    names: dict[str, str] = {}


@app.get("/api/me/watchlist")
async def api_get_watchlist(request: Request) -> dict[str, Any]:
    """读取当前账号的自选股；无记录返回 empty=true，前端据此用当前/默认列表做种子。"""
    claims = require_current_user(request)
    data = get_user_watchlist(str(claims.get("sub", "")))
    if data is None:
        return {"symbols": [], "names": {}, "empty": True}
    return {**data, "empty": False}


@app.post("/api/me/watchlist")
async def api_save_watchlist(request: Request, payload: WatchlistPayload) -> dict[str, Any]:
    """整表保存当前账号的自选股（前端持全量，覆盖式写入）。"""
    claims = require_current_user(request)
    return set_user_watchlist(str(claims.get("sub", "")), payload.symbols, payload.names)


# ===== 轻互动：资讯一键表态（看多/看空）+ 收藏 =====
@app.post("/api/news/react")
async def api_news_react(request: Request) -> dict[str, Any]:
    """对一条资讯表态（看多/看空，再点取消）。登录态。返回该条聚合 {bull,bear,mine}。"""
    claims = require_current_user(request)
    body = await request.json()
    return engagement.react(str(claims.get("sub", "")), str(body.get("message_id", "")), str(body.get("stance", "")))


@app.post("/api/news/reactions")
async def api_news_reactions(request: Request) -> dict[str, Any]:
    """批量取多条资讯的聚合情绪 {mid:{bull,bear,mine}}。公开（匿名也可看计数，mine 为 null）。"""
    claims = current_claims(request)
    uid = str(claims.get("sub", "")) if claims else None
    try:
        body = await request.json()
        ids = body.get("ids") or []
    except Exception:
        ids = []
    return {"reactions": engagement.reactions_for(list(ids), uid)}


@app.post("/api/me/bookmark")
async def api_bookmark_toggle(request: Request) -> dict[str, Any]:
    """收藏/取消收藏一条资讯（登录态）。返回 {bookmarked,count}。"""
    claims = require_current_user(request)
    body = await request.json()
    return engagement.toggle_bookmark(
        str(claims.get("sub", "")), str(body.get("message_id", "")),
        title=str(body.get("title", "")), topic=str(body.get("topic", "")),
        url=str(body.get("url", "")), symbol=str(body.get("symbol", "")),
    )


@app.get("/api/me/bookmarks")
async def api_bookmarks_list(request: Request) -> dict[str, Any]:
    """我的收藏列表（登录态，新→旧）。"""
    claims = require_current_user(request)
    return {"items": engagement.list_bookmarks(str(claims.get("sub", "")))}


@app.get("/api/system/readiness", response_model=SystemReadinessResponse)
async def system_readiness() -> SystemReadinessResponse:
    checks = _build_system_readiness_checks()
    weights = {
        "model": 18,
        "auth": 18,
        "agent_worker": 12,
        "data_sources": 14,
        "market_data": 10,
        "mcp_governance": 10,
        "cors": 10,
        "storage": 8,
    }
    score = 0.0
    for check in checks:
        weight = weights.get(check.key, 0)
        if check.status == "pass":
            score += weight
        elif check.status == "warn":
            score += weight * 0.5

    rounded_score = int(round(max(0, min(100, score))))
    blockers = [check.name for check in checks if check.status == "fail"]
    warnings = [check.name for check in checks if check.status == "warn"]
    status_label = "ready" if rounded_score >= 85 and not blockers else "degraded" if rounded_score >= 55 else "not_ready"
    return SystemReadinessResponse(
        status=status_label,
        score=rounded_score,
        generated_at=datetime.now(timezone.utc),
        checks=checks,
        blockers=blockers,
        warnings=warnings,
    )


def _build_system_readiness_checks() -> list[SystemReadinessCheck]:
    model_config = public_model_config()
    sources = list_data_sources()
    mcp_servers = list_mcp_servers()
    origins = _allowed_origins()
    quote_key_configured = bool(
        os.getenv("FINNHUB_API_KEY")
        or os.getenv("FINNHUB_TOKEN")
        or os.getenv("ALPHAVANTAGE_API_KEY")
        or os.getenv("ALPHA_VANTAGE_API_KEY")
    )
    auth_enforced = auth_required()
    try:
        registered_users = count_users()
    except Exception:
        registered_users = 0
    from . import storage as core_storage

    managed_database = core_storage.is_managed_database()
    persistent_db_paths = [
        os.getenv("DEEPFOCUS_AGENT_DB_PATH"),
        os.getenv("DEEPFOCUS_DATA_SOURCE_DB_PATH"),
        os.getenv("DEEPFOCUS_MCP_DB_PATH"),
    ]
    using_explicit_storage = managed_database or any(path for path in persistent_db_paths)
    high_risk_mcp_without_approval = [
        server.name for server in mcp_servers
        if server.risk_level == "high" and not server.approval_required
    ]

    return [
        SystemReadinessCheck(
            key="model",
            name="真实模型通道",
            status="pass" if model_config.provider != "mock" and model_config.api_key_configured else "fail",
            detail=(
                f"当前模型：{model_config.provider}/{model_config.model}。"
                if model_config.provider != "mock" and model_config.api_key_configured
                else "当前仍是 mock 或缺少 API Key，AI 投研不能作为真实结论链路。"
            ),
            remediation="在 FinGPT → 模型配置中保存真实模型、Base URL 和 API Key。",
        ),
        SystemReadinessCheck(
            key="auth",
            name="认证与权限",
            status=(
                "pass" if auth_enforced and registered_users > 0
                else "warn" if auth_enforced
                else "fail"
            ),
            detail=(
                f"已启用 JWT 认证 + RBAC，已注册 {registered_users} 个账号。"
                if auth_enforced and registered_users > 0
                else "已启用强制认证，但尚无账号；请注册或预置管理员。"
                if auth_enforced
                else "当前 API 未启用强制认证（演示态）；JWT/RBAC 通道已就绪，置 DEEPFOCUS_AUTH_REQUIRED=true 即生效。"
            ),
            remediation="设置 DEEPFOCUS_AUTH_REQUIRED=true、DEEPFOCUS_JWT_SECRET，并预置管理员（DEEPFOCUS_ADMIN_EMAIL/PASSWORD）。",
        ),
        SystemReadinessCheck(
            key="agent_worker",
            name="Agent Worker",
            status="pass" if is_worker_running() else "fail",
            detail="投研任务 worker 正在运行。" if is_worker_running() else "投研任务 worker 未运行。",
            remediation="启动 FastAPI 后端并确认 lifespan 能正常启动 worker。",
        ),
        SystemReadinessCheck(
            key="data_sources",
            name="证据数据源",
            status="pass" if len(sources) >= 2 else "warn" if sources else "fail",
            detail=f"已注册 {len(sources)} 个数据源。",
            remediation="至少配置行情、公告/财报、新闻/社区、研报文件等可追溯数据源。",
        ),
        SystemReadinessCheck(
            key="market_data",
            name="行情主数据",
            status="pass" if quote_key_configured else "warn",
            detail=(
                "已配置 Finnhub/Alpha Vantage 等主行情 key。"
                if quote_key_configured
                else "未配置主行情 key，会依赖免费公共 fallback，时效和稳定性不足。"
            ),
            remediation="配置 FINNHUB_API_KEY、ALPHAVANTAGE_API_KEY 或接入本地授权行情源。",
        ),
        SystemReadinessCheck(
            key="mcp_governance",
            name="MCP 工具治理",
            status="fail" if high_risk_mcp_without_approval else "pass" if mcp_servers else "warn",
            detail=(
                f"高风险 MCP 未开启审批：{', '.join(high_risk_mcp_without_approval[:3])}。"
                if high_risk_mcp_without_approval
                else f"已登记 {len(mcp_servers)} 个 MCP server。"
            ),
            remediation="高风险 MCP 必须开启人工确认、allow-list 和调用审计。",
        ),
        SystemReadinessCheck(
            key="cors",
            name="CORS 边界",
            status="fail" if "*" in origins else "pass",
            detail="CORS 当前允许任意来源。" if "*" in origins else f"CORS 限定在 {len(origins)} 个来源。",
            remediation="生产环境设置 CORS_ORIGINS 为明确域名，避免使用 *。",
        ),
        SystemReadinessCheck(
            key="storage",
            name="持久化配置",
            status="pass" if using_explicit_storage else "warn",
            detail=(
                "已接入托管数据库（DEEPFOCUS_DATABASE_URL），认证子系统已运行其上。"
                if managed_database
                else "已显式配置核心 SQLite/存储路径。"
                if using_explicit_storage
                else "当前使用默认本地 SQLite 文件，适合单机演示，不适合多用户机构部署。"
            ),
            remediation="生产环境迁移到托管数据库/对象存储，并配置备份、迁移和权限。",
        ),
    ]


@app.api_route(
    "/research-workbench",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def research_workbench_root(request: Request):
    return await proxy_research_workbench(request, "")


@app.api_route(
    "/research-workbench/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def research_workbench_proxy(path: str, request: Request):
    return await proxy_research_workbench(request, path)


@app.get("/api/fingpt/capabilities", response_model=CapabilityListResponse)
async def capabilities() -> CapabilityListResponse:
    return llm.capabilities()


@app.get("/api/fingpt/model-config", response_model=ModelConfigResponse)
async def get_model_config() -> ModelConfigResponse:
    return public_model_config()


@app.post("/api/fingpt/model-config", response_model=ModelConfigResponse)
async def update_model_config(request: ModelConfigRequest) -> ModelConfigResponse:
    return save_model_config(request)


@app.get("/api/market/quotes", response_model=MarketQuoteListResponse)
async def market_quotes(request: Request, symbols: str = "") -> MarketQuoteListResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    # 白名单账号(lx199710)的 A股自选用 iFinD 实时增强（灰度）；其他用户/匿名完全走原链。
    return attach_data_quality(await fetch_market_quotes(requested_symbols, ifind_user=ifind_enhance_enabled(request)))


@app.get("/api/market/search", response_model=MarketSymbolSearchResponse)
async def market_symbol_search(q: str = "", market: Optional[str] = None) -> MarketSymbolSearchResponse:
    return await search_market_symbols(q, market=market)


@app.get("/api/market/data-layers", response_model=MarketDataLayerStatusResponse)
async def market_data_layers(symbol: Optional[str] = None, keyword: Optional[str] = None) -> MarketDataLayerStatusResponse:
    return await build_market_data_layer_status(symbol=symbol, keyword=keyword)


@app.get("/api/market/ashare/structured", response_model=AShareStructuredDataResponse)
async def ashare_structured_data(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 120,
) -> AShareStructuredDataResponse:
    return await fetch_ashare_structured_data(
        symbol,
        start_date=start_date,
        end_date=end_date,
        limit=max(1, min(limit, 500)),
    )


@app.get("/api/options/signals", response_model=OptionsSignalResponse)
async def options_signals(
    symbols: str = "",
    horizon_days: int = 45,
    max_expirations: int = 3,
) -> OptionsSignalResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return attach_data_quality(await fetch_options_signals(
        requested_symbols,
        horizon_days=horizon_days,
        max_expirations=max_expirations,
    ))


@app.post("/api/options/ai-analysis", response_model=OptionsAiAnalysisResponse)
async def options_ai_analysis(request: OptionsAiAnalysisRequest) -> OptionsAiAnalysisResponse:
    return attach_data_quality(await llm.analyze_options_trend(request))


@app.get("/api/earnings/calendar", response_model=EarningsCalendarResponse)
async def earnings_calendar(
    symbols: str = "",
    horizon: str = "3month",
    min_market_cap: Optional[float] = None,
    include_all: bool = False,
) -> EarningsCalendarResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return attach_data_quality(await fetch_earnings_calendar(
        requested_symbols,
        horizon=horizon,
        min_market_cap=min_market_cap,
        include_all=include_all,
    ))


@app.post("/api/decision/multi-market", response_model=MultiMarketDecisionResponse)
async def multi_market_decision(request: MultiMarketDecisionRequest) -> MultiMarketDecisionResponse:
    # 用真实准实时行情刷新候选的现价/涨跌幅，让规则评分跑在真数据上，
    # 而非信任前端可能过期的快照或硬编码示例。A股/港股经东财/新浪可达，美股 best-effort。
    enriched_count = 0
    symbols = [s.symbol for s in (request.stocks or []) if s.symbol]
    if symbols:
        try:
            quote_resp = await fetch_market_quotes(symbols)
            qmap: dict[str, Any] = {}
            for q in quote_resp.quotes:
                sym = (q.symbol or "").upper()
                qmap[sym] = q
                qmap.setdefault(sym.split(".")[0], q)  # 容错：按去后缀的 base 也建一个键
            updated = []
            for s in request.stocks:
                key = (s.symbol or "").upper()
                q = qmap.get(key) or qmap.get(key.split(".")[0])
                if q and q.price:
                    updated.append(s.model_copy(update={
                        "current_price": q.price,
                        "change_percent": q.change_percent if q.change_percent is not None else s.change_percent,
                    }))
                    enriched_count += 1
                else:
                    updated.append(s)
            request = request.model_copy(update={"stocks": updated})
        except Exception:
            pass

    response = build_multi_market_decision(request)
    reasons: list[str] = []
    if enriched_count > 0:
        response.provider = "multi-market-rule"
        reasons.append(f"已用真实准实时行情刷新 {enriched_count}/{len(symbols)} 只候选的现价与涨跌幅，规则评分基于真数据。")
    else:
        reasons.append("未获取到候选的实时行情，评分基于传入快照或示例数据，请谨慎参考。")
    reasons.append("模块就绪度、依赖与回测计划为能力规划（规划中），非已落地的真实回测结果。")
    return attach_data_quality(response, reasons=reasons)


@app.get("/api/agents/premarket-opportunities", response_model=PremarketOpportunityResponse)
async def premarket_opportunities() -> PremarketOpportunityResponse:
    return attach_data_quality(await build_premarket_opportunity_radar())


def _market_quote_from_google(g: dict) -> MarketQuote:
    """把 Google Finance 抓取的真实行情 dict 转成 MarketQuote（provider=google-finance → degraded）。"""
    return MarketQuote(
        symbol=g["symbol"],
        price=g["price"],
        change=g.get("change"),
        change_percent=g.get("change_percent"),
        previous_close=g.get("previous_close"),
        open_price=g.get("open"),
        high=g.get("high"),
        low=g.get("low"),
        volume=g.get("volume"),
        currency=g.get("currency", "USD"),
        provider="google-finance",
        provider_name="Google Finance",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_realtime=False,
        delay_note="Google Finance 网页行情，可能延迟约 15 分钟",
    )


def _market_quote_from_yahoo(g: dict) -> MarketQuote:
    """把 Yahoo Finance 官方行情 dict 转成 MarketQuote（provider=yahoo-finance → live、官方实时）。"""
    prev = g.get("previous_close")
    price = g.get("price")
    change = round(price - prev, 4) if (price and prev) else None
    pct = round(change / prev * 100, 4) if (change is not None and prev) else None
    return MarketQuote(
        symbol=g["symbol"],
        price=price,
        change=change,
        change_percent=pct,
        previous_close=prev,
        high=g.get("high"),
        low=g.get("low"),
        volume=g.get("volume"),
        currency=g.get("currency", "USD"),
        provider="yahoo-finance",
        provider_name="Yahoo Finance",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_realtime=True,
        wk52_high=g.get("wk52_high"),
        wk52_low=g.get("wk52_low"),
    )


def _market_quote_from_ifind(row: dict) -> Optional[MarketQuote]:
    """同花顺 iFinD A股实时行情 dict → MarketQuote（provider=ifind → 经 classify 判 live）。
    latest 缺失/非正 → 返回 None（视为未命中，交回退链）。"""
    latest = row.get("latest")
    if not isinstance(latest, (int, float)) or latest <= 0:
        return None
    pct = row.get("changeRatio")
    prev = round(latest / (1 + pct / 100), 4) if isinstance(pct, (int, float)) and pct != -100 else None
    change = round(latest - prev, 4) if prev is not None else None
    return MarketQuote(
        symbol=row.get("code"),
        price=latest,
        change=change,
        change_percent=pct if isinstance(pct, (int, float)) else None,
        previous_close=prev,
        high=row.get("high"),
        low=row.get("low"),
        volume=row.get("volume"),
        currency="CNY",
        provider="ifind",
        provider_name="同花顺 iFinD",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_realtime=True,
    )


async def _enhance_tear_sheet_narrative(ts):
    """用 LLM 把确定性 7 维证据合成 2-3 句买方观点；mock/失败/超时回退模板（verdict/score 不变）。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return ts
        narrative = await llm.synthesize_tear_sheet_narrative(ts)
        if narrative:
            ts.narrative = narrative
            ts.narrative_provider = llm.provider
    except Exception:
        pass  # LLM 不可用 → 保留确定性模板叙述
    return ts


async def _enhance_review_narrative(obj, *, view, subject):
    """组合/宏观速判：用 LLM 合成 narrative；mock/失败回退模板（verdict/score 不变）。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return obj
        narrative = await llm.synthesize_review_narrative(
            subject=subject,
            verdict=obj.overall_verdict,
            score=getattr(obj, "overall_score", getattr(obj, "risk_score", 0)),
            confidence=obj.confidence,
            dimensions=obj.dimensions,
            view=view,
        )
        if narrative:
            obj.narrative = narrative
            obj.narrative_provider = llm.provider
    except Exception:
        pass
    return obj


async def _enhance_briefing_headline(briefing):
    """投研晨报：用 LLM 把宏观×组合×自选股合成晨会纪要 headline；mock/失败回退模板。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return briefing
        headline = await llm.synthesize_briefing_headline(
            briefing.macro, briefing.portfolio, getattr(briefing, "watchlist", None)
        )
        if headline:
            briefing.headline = headline
            briefing.headline_provider = llm.provider
    except Exception:
        pass
    return briefing


async def _build_stock_tear_sheet_core(
    symbol: str,
    name: str = "",
    market_cap: Optional[float] = None,
    market: str = "",
    use_ifind: bool = False,
    persist: bool = True,
) -> TearSheetResponse:
    """聚合行情/财报/期权多源证据，由确定性引擎逐维度判定，返回未经 LLM 叙述增强的速判卡。

    每块数据缺失时诚实标 insufficient，整体可信度取各维度最差档（引擎已算，不经 attach 覆盖）。
    端点会叠加 LLM 叙述；tool-use agent 的 get_stock_verdict 直接调用本函数（不触发 LLM，避免递归）。
    """
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol 不能为空")

    import asyncio

    from .eastmoney_data import fetch_eastmoney_earnings, fetch_eastmoney_index, fetch_fund_flow
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .consensus_source import fetch_analyst_consensus
    from .valuation_source import fetch_valuation
    from .yahoo_finance import fetch_yahoo_history, fetch_yahoo_quote

    _mkt = (market or "").upper()
    if not _mkt and sym.isdigit():
        _mkt = "CN" if len(sym) == 6 else "HK"  # 6位数字→A股，其余纯数字→港股
    _cn_secid = "1.000300" if _mkt == "CN" else ("100.HSI" if _mkt == "HK" else None)

    async def _safe(coro, default):
        try:
            return await coro
        except Exception:
            return default

    # 所有真实数据源并行抓取（串行约 10s → 并行约 2s），各源独立容错、互不阻塞。
    (
        gquote,
        price_history,
        nasdaq_eps,
        nasdaq_opts,
        eastmoney_fin,
        sp500_idx,
        rates_history,
        constituent,
        cn_idx,
        valuation_data,
        yquote,
        consensus_data,
        fund_flow_data,
    ) = await asyncio.gather(
        _safe(fetch_google_finance_quote(sym, market or None), None),
        _safe(fetch_yahoo_history(sym, market or None), []),
        _safe(fetch_nasdaq_earnings(sym), None),
        _safe(fetch_nasdaq_options(sym), None),
        _safe(fetch_eastmoney_earnings(sym, market), None),
        _safe(fetch_sp500_index_history(), []),
        _safe(fetch_us10y_history(), []),
        _safe(fetch_sp500_constituent(sym), None),
        _safe(fetch_eastmoney_index(_cn_secid), []) if _cn_secid else _safe(asyncio.sleep(0, result=[]), []),
        _safe(fetch_valuation(sym, market), None),
        _safe(fetch_yahoo_quote(sym, market or None), None),
        _safe(fetch_analyst_consensus(sym, market or None), None),
        _safe(fetch_fund_flow(sym, market or None), None),
    )

    # 行情优先 Yahoo 官方（live），限流/失败回退 Google Finance（degraded）。
    quote = _market_quote_from_yahoo(yquote) if yquote else (_market_quote_from_google(gquote) if gquote else None)
    earnings_events: list = []
    options_signal = None

    # iFinD A股增强（授权数据，禁转卖→只作"内部燃料"补强，且不落公开存储，见下方 persist 守卫）：
    #  · 估值/规模(scale/valuation)：对【所有】A股请求用 iFinD 基本面补强(PE/PB/市值)，带 5min 缓存护配额；
    #  · 实时行情(quote)覆盖：仅【白名单 use_ifind】，其余人行情仍走东财/Yahoo（用户拍板"折中:仅基本面补强"）。
    #  失败/超时/非命中 → 无缝回退原链，绝不拖累、不报错。
    ifind_val: Optional[dict] = None
    if _mkt == "CN":
        from . import ifind_api
        if ifind_api.enabled():
            try:
                _row = await asyncio.wait_for(asyncio.to_thread(ifind_api.cached_single_quote, sym), timeout=6.0)
            except Exception:  # noqa: BLE001
                _row = None
            if _row:
                ifind_val = {"market_cap": _row.get("totalCapital"), "pe_ratio": _row.get("pe_ttm"),
                             "pb_ratio": _row.get("pb"), "provider": "ifind"}
                if use_ifind:  # 白名单才用 iFinD 覆盖实时行情；其余仅估值补强
                    _iq = _market_quote_from_ifind(_row)
                    if _iq:
                        quote = _iq  # provider=ifind → 各维度按 quote.provider 自动判 live

    # CN/HK：Yahoo 历史在本环境 403 → price_history 空，用东财个股日线兜底（价格趋势图转 live），
    # 并用日线 min/max 补 52周区间（quote 缺失 52周时）。
    if _mkt in ("CN", "HK") and not price_history:
        from .eastmoney_data import _em_stock_secid, fetch_eastmoney_kline

        _stk_secid = _em_stock_secid(sym, _mkt)
        if _stk_secid:
            price_history = await _safe(fetch_eastmoney_kline(_stk_secid), [])
            if price_history and quote is not None and not quote.wk52_high:
                _closes = [v for _, v in price_history]
                if _closes:
                    quote.wk52_high = max(_closes)
                    quote.wk52_low = min(_closes)

    # 市场环境维度按 market 选本土大盘：A股→沪深300、港股→恒生（东财），其余→标普500。
    market_index_name = "标普500"
    market_provider_tag = "github-sp500"
    market_source = None
    market_index_history = sp500_idx
    if _mkt in ("CN", "HK") and cn_idx:
        from .tear_sheet import _CN_INDEX_SRC

        market_index_name = "沪深300" if _mkt == "CN" else "恒生指数"
        market_provider_tag = "eastmoney"
        market_source = _CN_INDEX_SRC
        market_index_history = cn_idx

    _val = ifind_val or valuation_data  # iFinD 增强命中时，估值/规模维度用同花顺数据（provider=ifind → live）
    ts = build_tear_sheet(
        symbol=sym,
        name=name or (constituent or {}).get("name") or sym,
        market_cap=market_cap or (_val or {}).get("market_cap") or (gquote.get("market_cap") if gquote else None),
        currency=(quote.currency if quote else "USD"),
        quote=quote,
        earnings_events=earnings_events,
        options_signal=options_signal,
        market_index_history=market_index_history,
        rates_history=rates_history,
        constituent=constituent,
        valuation=gquote,
        valuation_data=_val,
        consensus_data=consensus_data,
        fund_flow_data=fund_flow_data,
        price_history=price_history,
        nasdaq_eps=nasdaq_eps,
        nasdaq_opts=nasdaq_opts,
        eastmoney_fin=eastmoney_fin,
        market_index_name=market_index_name,
        market_source=market_source,
        market_provider_tag=market_provider_tag,
    )
    # 写穿持久化数据层：每次构建速判卡记一条 verdict 数据点（历史积累；失败静默，不影响主流程）。
    # ⚠️ 凡用到 iFinD 数据(白名单 quote 覆盖 use_ifind，或全员估值补强 ifind_val)绝不写入共享 verdict——
    # 该数据点被零鉴权 /api/data/history 与公开 SEO 页读取，写入会把 iFinD 衍生数据泄漏对外（违授权红线 + 破隔离）。
    if persist and not use_ifind and ifind_val is None:
        record_datapoint(
            "verdict", sym,
            {
                "verdict": ts.overall_verdict,
                "score": ts.overall_score,
                "confidence": ts.confidence,
                "price": ts.price,
                "change_percent": ts.change_percent,
                "currency": ts.currency,
            },
            market=_mkt,
        )
    return ts


@app.get("/api/stock/tear-sheet", response_model=TearSheetResponse)
async def stock_tear_sheet(
    request: Request,
    symbol: str,
    name: str = "",
    market_cap: Optional[float] = None,
    market: str = "",
) -> TearSheetResponse:
    """个股速判卡：聚合多源证据由确定性引擎逐维度判定，再叠加 LLM 买方叙述。
    白名单账号(lx199710)的 A股卡用 iFinD 实时数据增强(灰度)；其他用户/路径完全不变。"""
    ts = await _build_stock_tear_sheet_core(
        symbol, name=name, market_cap=market_cap, market=market,
        use_ifind=ifind_enhance_enabled(request),
    )
    return await _enhance_tear_sheet_narrative(ts)


# 速判卡读缓存 TTL（秒）：结论分钟级不变，agent 同一会话反复研判同一标的时免去重建 13 个并发源。
# 仅作用于工具路径，用户端点 /api/stock/tear-sheet 始终实时（不读缓存）。
_VERDICT_TOOL_CACHE_TTL = 120.0


async def _tool_get_stock_verdict(symbol: str, market: Optional[str] = None) -> Any:
    """工具：返回确定性引擎的速判卡结论（不触发 LLM 叙述）。verdict/score/各维度信号均为 ground truth。

    短 TTL 读缓存（data_store）：命中则直接返回上次结果（标 cached=True），避免重复重建 13 个并发请求。
    """
    sym = (symbol or "").strip().upper()
    cache_key = f"{sym}|{(market or '').upper()}"
    cached = data_latest("verdict_tool", cache_key, max_age_seconds=_VERDICT_TOOL_CACHE_TTL)
    if cached:
        return {**cached, "cached": True}

    ts = await _build_stock_tear_sheet_core(symbol, market=market or "")
    result = {
        "symbol": ts.symbol,
        "name": ts.name,
        "price": ts.price,
        "change_percent": ts.change_percent,
        "currency": ts.currency,
        "overall_verdict": ts.overall_verdict,
        "overall_score": ts.overall_score,
        "confidence": ts.confidence,
        "data_quality": ts.data_quality.model_dump() if ts.data_quality else None,
        "dimensions": [
            {
                "label": dim.label,
                "signal": dim.signal,
                "score": dim.score,
                "headline": dim.headline,
                "confidence": dim.confidence,
            }
            for dim in ts.dimensions
        ],
    }
    record_datapoint("verdict_tool", cache_key, result)  # 写入缓存（同时供历史）
    return result


register_tool(AgentTool(
    name="get_stock_verdict",
    description=(
        "获取个股速判卡：确定性引擎逐维度判定的综合结论（看多/看空/中性）、评分、置信度与各维度证据信号。"
        "这是平台核心结论（ground truth，非模型臆测），回答个股研判/是否值得买/综合看法类问题时应优先调用。美/A/港股通用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 AAPL、600519、00700"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"], "description": "市场；缺省按代码推断"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_stock_verdict,
))


async def _tool_get_verdict_history(symbol: str, market: Optional[str] = None) -> Any:
    """工具：读取该标的速判卡结论随时间的历史（来自持久化数据层，随平台运行积累）。"""
    sym = (symbol or "").strip().upper()
    items = data_history("verdict", sym, limit=20)
    return {
        "symbol": sym,
        "count": len(items),
        "history": [
            {"at": it.get("recorded_at"), **(it["payload"] if isinstance(it.get("payload"), dict) else {})}
            for it in items
        ],
        "note": "速判卡结论历史（每次构建速判卡时积累）；为空表示该标的尚无历史快照。",
    }


register_tool(AgentTool(
    name="get_verdict_history",
    description=(
        "获取个股速判卡结论（看多/看空/评分/价格）随时间的历史演变，用于回答「最近评级怎么变的/趋势如何/上次看法」。"
        "数据由持久化层随平台运行积累，可能为空（首次查询某标的时）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 AAPL、600519、00700"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"], "description": "市场；缺省按代码推断"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_verdict_history,
))


_SYMBOL_MARKET_TOOL_PARAMS = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码，如 AAPL、600519、00700"},
        "market": {"type": "string", "enum": ["US", "CN", "HK"], "description": "市场；缺省按代码推断"},
    },
    "required": ["symbol"],
}


async def _tool_get_price_history(symbol: str, market: Optional[str] = None) -> Any:
    """工具：近 ~6 个月价格走势摘要（首/末/高/低/区间涨跌幅）。"""
    from .yahoo_finance import fetch_yahoo_history

    series = await fetch_yahoo_history(symbol, market or None)
    closes = [c for _, c in series] if series else []
    if not closes:
        return None
    first, last = closes[0], closes[-1]
    return {
        "symbol": (symbol or "").strip().upper(),
        "points": len(closes),
        "period_start": series[0][0],
        "period_end": series[-1][0],
        "first": first,
        "last": last,
        "high": max(closes),
        "low": min(closes),
        "change_pct": round((last - first) / first * 100, 2) if first else None,
        "note": "近 ~6 个月日线收盘摘要（Yahoo，本环境可能受限）。",
    }


async def _tool_get_macro_environment(symbol: str = "", market: Optional[str] = None) -> Any:
    """工具：当前宏观环境快照——10年美债收益率 / WTI 原油 / 黄金 / 标普500 的最新值。"""
    from .github_data import (
        fetch_gold_history,
        fetch_oil_history,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )

    us10y, oil, gold, sp500 = await asyncio.gather(
        fetch_us10y_history(),
        fetch_oil_history(),
        fetch_gold_history(),
        fetch_sp500_index_history(),
        return_exceptions=True,
    )

    def _last(series: Any) -> Any:
        if isinstance(series, list) and series:
            try:
                date, value = series[-1]  # 每元素应为 (date, value) 2-元组
                return {"date": date, "value": value}
            except (ValueError, TypeError):
                return None
        return None

    snap = {
        "us10y_yield": _last(us10y),
        "wti_oil": _last(oil),
        "gold": _last(gold),
        "sp500": _last(sp500),
        "note": "宏观快照最新值（GitHub 公共数据源）；与个股无关，symbol 参数可忽略。",
    }
    return snap if any(snap[k] for k in ("us10y_yield", "wti_oil", "gold", "sp500")) else None


async def _tool_get_options_signal(symbol: str, market: Optional[str] = None) -> Any:
    """工具：期权情绪信号（Put/Call 等）。仅美股有覆盖。"""
    from .nasdaq_data import fetch_nasdaq_options

    return await fetch_nasdaq_options((symbol or "").strip().upper())


register_tool(AgentTool(
    name="get_price_history",
    description="获取个股近 ~6 个月价格走势摘要（区间涨跌幅、最高/最低、起止价），用于回答「走势如何/这段涨跌多少/在高位还是低位」。",
    parameters=_SYMBOL_MARKET_TOOL_PARAMS,
    handler=_tool_get_price_history,
))
register_tool(AgentTool(
    name="get_macro_environment",
    description=(
        "获取当前宏观环境快照：10年美债收益率、WTI原油、黄金、标普500 的最新值。"
        "回答宏观/利率/油价/避险/大盘环境类问题时**必须优先调用本工具**（它直接给出这些权威数值），"
        "不要改用个股行情工具去代理大盘指数。无需 symbol 参数。"
    ),
    parameters={"type": "object", "properties": {}},
    handler=_tool_get_macro_environment,
))
register_tool(AgentTool(
    name="get_options_signal",
    description="获取个股期权情绪信号（Put/Call 等），用于判断市场对冲/投机情绪。仅美股有覆盖。",
    parameters=_SYMBOL_MARKET_TOOL_PARAMS,
    handler=_tool_get_options_signal,
))


async def _tool_get_stock_comparison(symbols: str = "") -> Any:
    """多只股票横向对比：复用速判引擎逐维度信号灯矩阵 + 综合评分。symbols 逗号分隔(最多6只)。"""
    syms = ",".join([s.strip() for s in (symbols or "").split(",") if s.strip()][:6])
    if not syms:
        return None
    resp = await stock_compare(symbols=syms)
    items = [
        {"symbol": it.symbol, "name": it.name, "verdict": it.overall_verdict,
         "score": it.overall_score, "sector": it.sector, "market_cap": it.market_cap,
         "dims": {d.label: d.signal for d in (it.dimensions or [])}}
        for it in (resp.items or [])
    ]
    return {"items": items} if items else None


register_tool(AgentTool(
    name="get_stock_comparison",
    description=(
        "多只股票横向对比（本平台速判引擎逐维度信号灯矩阵 + 综合评分/评级）。"
        "用户问『A 和 B 哪个好/对比一下这几只/谁更值得买』时用。symbols 逗号分隔，最多 6 只(如 600519,000858)。"
    ),
    parameters={
        "type": "object",
        "properties": {"symbols": {"type": "string", "description": "逗号分隔的股票代码，最多6只"}},
        "required": ["symbols"],
    },
    handler=_tool_get_stock_comparison,
))


async def _tool_get_briefing_today() -> Any:
    """投研晨报：市场环境速判 + 组合风险 → 买方一句话行动建议。读缓存(30min)秒回，缺则现算一次再缓存。"""
    from . import data_store
    cached = data_store.latest("wx_briefing", "TODAY", max_age_seconds=1800)
    if isinstance(cached, dict) and cached:
        return cached
    from .risk_management import get_risk_summary
    inputs = await _gather_macro_inputs()
    macro = build_macro_review(**inputs)
    portfolio = build_portfolio_review(
        get_risk_summary(), sp500_history=inputs["sp500_history"], rates_history=inputs["rates_history"],
    )
    b = build_briefing(macro, portfolio)
    out = {"headline": b.headline, "macro_verdict": b.macro_verdict, "portfolio_verdict": b.portfolio_verdict}
    try:
        data_store.record("wx_briefing", "TODAY", out)
    except Exception:  # noqa: BLE001
        pass
    return out


register_tool(AgentTool(
    name="get_briefing_today",
    description=(
        "获取本平台投研晨报：市场环境(风险偏好/中性/避险)+ 组合风险 → 买方晨会一句话行动建议。"
        "用户问『今天晨报/买方观点/现在市场环境怎么样/该进还是该防』时用。无需参数。"
    ),
    parameters={"type": "object", "properties": {}},
    handler=_tool_get_briefing_today,
))


@app.get("/api/portfolio/review", response_model=PortfolioReviewResponse)
async def portfolio_review() -> PortfolioReviewResponse:
    """组合风险速判：基于本地持仓与风控摘要的买方视角一页纸（集中度/行业敞口/回撤/止损纪律）。"""
    from .github_data import fetch_sp500_index_history, fetch_us10y_history
    from .risk_management import get_risk_summary

    sp500: list = []
    rates: list = []
    try:
        sp500 = await fetch_sp500_index_history()
        rates = await fetch_us10y_history()
    except Exception:
        sp500, rates = [], []

    summary = get_risk_summary()
    # 有持仓才拉实时价（空仓零开销）；用 Google Finance 准实时价刷新 current_price 后重算盈亏/回撤。
    if summary.get("open_positions"):
        try:
            from .risk_management import apply_live_prices, fetch_live_prices_for_positions

            price_map = await fetch_live_prices_for_positions(summary["open_positions"])
            summary = apply_live_prices(summary, price_map)
        except Exception:
            pass
    review = build_portfolio_review(summary, sp500_history=sp500, rates_history=rates)
    return await _enhance_review_narrative(review, view="portfolio", subject="组合")


async def _gather_macro_inputs() -> dict:
    """宏观速判/晨报共用的真实数据输入：github 月度（市场/利率/油/金）+ 美国看板 live 六件套 + 中国宏观四件套。

    六路并发拉取，任一失败诚实降级为空（由速判侧标 insufficient），不互相阻断。
    """
    from .github_data import (
        fetch_gold_history,
        fetch_oil_history,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .market_dashboard import get_china_macro_indicators, get_macro_risk_indicators

    async def _safe(coro):
        try:
            return await coro
        except Exception:
            return None

    # github 四路 + 美国看板与 github 不同源可并发；中国看板与美国看板共享 Sina/东财上游，
    # 冷取并发会互相饿死（实测 china 返回空），故待美国看板完成后再取，二者各自 120/300s 缓存兜住时延。
    sp500, rates, oil, gold, risk = await asyncio.gather(
        _safe(fetch_sp500_index_history()),
        _safe(fetch_us10y_history()),
        _safe(fetch_oil_history()),
        _safe(fetch_gold_history()),
        _safe(get_macro_risk_indicators()),
    )
    china = await _safe(get_china_macro_indicators())
    return {
        "sp500_history": sp500 or [],
        "rates_history": rates or [],
        "oil_history": oil or [],
        "gold_history": gold or [],
        "risk_indicators": risk or {},
        "china_indicators": china or {},
    }


@app.get("/api/macro/review", response_model=MacroReviewResponse)
async def macro_review() -> MacroReviewResponse:
    """宏观环境速判：市场/波动率/利率/收益率曲线/信用利差/通胀/避险，全部真实公开数据。"""
    review = build_macro_review(**await _gather_macro_inputs())
    return await _enhance_review_narrative(review, view="macro", subject="宏观环境")


@app.get("/api/briefing/today", response_model=BriefingResponse)
async def briefing_today(symbols: str = "") -> BriefingResponse:
    """投研晨报：聚合宏观环境速判 + 组合风险速判，给买方晨会一页纸。

    复用同一份 github 行情（sp500/rates 供宏观与组合背景共用），整轮只拉一次。
    """
    from .github_data import fetch_sp500_constituent
    from .risk_management import get_risk_summary

    inputs = await _gather_macro_inputs()
    macro = build_macro_review(**inputs)
    portfolio = build_portfolio_review(
        get_risk_summary(),
        sp500_history=inputs["sp500_history"],
        rates_history=inputs["rates_history"],
    )
    briefing = build_briefing(macro, portfolio)

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:8]
    if syms:
        symbol_sectors: dict = {}
        for sym in syms:
            try:
                c = await fetch_sp500_constituent(sym)
                symbol_sectors[sym] = (c or {}).get("sector")
            except Exception:
                symbol_sectors[sym] = None
        briefing.watchlist = build_watchlist_summary(symbol_sectors, macro.overall_verdict)
    return await _enhance_briefing_headline(briefing)


@app.get("/api/stock/compare", response_model=StockCompareResponse)
async def stock_compare(symbols: str = "", caps: str = "") -> StockCompareResponse:
    """多标的横向对比：复用速判引擎做逐维度信号灯矩阵，共享同一份 github 市场/利率数据。

    个股实时行情受限时（动量/期权/催化）相关维度诚实显示数据不足；
    规模（market_cap）/行业（github 成分）/市场背景维度正常区分。
    """
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:6]
    cap_list = [c.strip() for c in caps.split(",")] if caps else []
    if not syms:
        return StockCompareResponse(generated_at=datetime.now(timezone.utc), items=[])

    sp500: list = []
    rates: list = []
    try:
        sp500 = await fetch_sp500_index_history()
    except Exception:
        sp500 = []
    try:
        rates = await fetch_us10y_history()
    except Exception:
        rates = []

    from .eastmoney_data import fetch_eastmoney_earnings
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .valuation_source import fetch_valuation

    async def _safe2(coro, default):
        try:
            return await coro
        except Exception:
            return default

    async def _compare_one(i: int, sym: str) -> StockCompareItem:
        cap = None
        if i < len(cap_list) and cap_list[i]:
            try:
                cap = float(cap_list[i])
            except ValueError:
                cap = None
        # 每标的多源并行抓取（含 valuation，让 scale/valuation 与单卡一致 live）；标的之间也并行。
        constituent, g, valuation_data, neps, nopts, efin = await asyncio.gather(
            _safe2(fetch_sp500_constituent(sym), None),
            _safe2(fetch_google_finance_quote(sym), None),
            _safe2(fetch_valuation(sym), None),
            _safe2(fetch_nasdaq_earnings(sym), None),
            _safe2(fetch_nasdaq_options(sym), None),
            _safe2(fetch_eastmoney_earnings(sym), None),
        )
        quote = _market_quote_from_google(g) if g else None
        gname = g.get("name") if g else None
        eff_cap = cap or (valuation_data or {}).get("market_cap") or (g.get("market_cap") if g else None)
        ts = build_tear_sheet(
            symbol=sym,
            name=gname or sym,
            market_cap=eff_cap,
            quote=quote,
            market_index_history=sp500,
            rates_history=rates,
            constituent=constituent,
            valuation=g,
            valuation_data=valuation_data,
            nasdaq_eps=neps,
            nasdaq_opts=nopts,
            eastmoney_fin=efin,
        )
        # 对比矩阵聚焦核心维度，个股深度维度（一致预期/资金面）不进 PK 列。
        dims = [d for d in ts.dimensions if d.key not in ("consensus", "fund_flow")]
        return StockCompareItem(
            symbol=sym,
            name=gname or sym,
            overall_verdict=ts.overall_verdict,
            overall_score=ts.overall_score,
            sector=(constituent or {}).get("sector"),
            market_cap=eff_cap,
            dimensions=dims,
            data_quality=ts.data_quality,
        )

    items = list(await asyncio.gather(*[_compare_one(i, sym) for i, sym in enumerate(syms)]))

    levels = [it.data_quality.level for it in items]
    worst = "mock" if "mock" in levels else ("degraded" if "degraded" in levels else "live")
    overall_dq = next((it.data_quality for it in items if it.data_quality.level == worst), items[0].data_quality)
    return StockCompareResponse(generated_at=datetime.now(timezone.utc), items=items, data_quality=overall_dq)


_SCREEN_DEFAULT = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META", "AVGO"]
_SCREEN_DIM_LABELS = {
    "momentum": "价格动量", "catalyst": "盈利质量", "options": "期权情绪",
    "scale": "规模", "valuation": "估值", "consensus": "一致预期",
    "fund_flow": "资金面", "market": "市场环境", "macro": "宏观利率",
}


@app.get("/api/stock/screen", response_model=StockScreenResponse)
async def stock_screen(query: str, symbols: str = "") -> StockScreenResponse:
    """自然语言选股：LLM 解析需求 → 对候选逐一跑速判引擎 → 按维度信号筛选并按命中数排序。

    候选标的间 + 每标的多源 双层并行；verdict/维度信号仍由确定性引擎判定，LLM 只负责把
    自然语言意图翻译成筛选条件（不参与个股判定）。
    """
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query 不能为空")
    import asyncio

    from .consensus_source import fetch_analyst_consensus
    from .eastmoney_data import fetch_eastmoney_earnings, fetch_fund_flow
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote
    from .llm import CloudResearchLLM
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .valuation_source import fetch_valuation

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:10] or _SCREEN_DEFAULT

    llm = CloudResearchLLM()
    parsed = None
    if llm.provider != "mock":
        try:
            parsed = await llm.parse_screen_query(q)
        except Exception:
            parsed = None
    if not parsed or not parsed.get("criteria"):
        return StockScreenResponse(
            generated_at=datetime.now(timezone.utc),
            query=q,
            criteria_summary="未能解析筛选条件（请在设置配置 AI 模型，或换更明确的表述）",
            provider="rule-template",
        )
    criteria = [
        StockScreenCriterion(dim=c["dim"], want=c.get("want", "bullish"), label=_SCREEN_DIM_LABELS[c["dim"]])
        for c in parsed["criteria"]
        if isinstance(c, dict) and c.get("dim") in _SCREEN_DIM_LABELS and c.get("want") in ("bullish", "bearish", "neutral")
    ]
    if not criteria:
        return StockScreenResponse(
            generated_at=datetime.now(timezone.utc),
            query=q,
            criteria_summary=parsed.get("summary", ""),
            provider=llm.provider,
        )

    async def _safe(coro, default):
        try:
            return await coro
        except Exception:
            return default

    sp500 = await _safe(fetch_sp500_index_history(), [])
    rates = await _safe(fetch_us10y_history(), [])

    async def _screen_one(sym: str) -> StockScreenMatch:
        constituent, g, valuation_data, neps, nopts, efin, cons, flow = await asyncio.gather(
            _safe(fetch_sp500_constituent(sym), None),
            _safe(fetch_google_finance_quote(sym), None),
            _safe(fetch_valuation(sym), None),
            _safe(fetch_nasdaq_earnings(sym), None),
            _safe(fetch_nasdaq_options(sym), None),
            _safe(fetch_eastmoney_earnings(sym), None),
            _safe(fetch_analyst_consensus(sym), None),
            _safe(fetch_fund_flow(sym), None),
        )
        quote = _market_quote_from_google(g) if g else None
        gname = g.get("name") if g else None
        eff_cap = (valuation_data or {}).get("market_cap") or (g.get("market_cap") if g else None)
        ts = build_tear_sheet(
            symbol=sym,
            name=gname or sym,
            market_cap=eff_cap,
            quote=quote,
            market_index_history=sp500,
            rates_history=rates,
            constituent=constituent,
            valuation=g,
            valuation_data=valuation_data,
            consensus_data=cons,
            fund_flow_data=flow,
            nasdaq_eps=neps,
            nasdaq_opts=nopts,
            eastmoney_fin=efin,
        )
        dim_by_key = {d.key: d for d in ts.dimensions}
        hit_labels: list = []
        miss_labels: list = []
        for c in criteria:
            dim = dim_by_key.get(c.dim)
            if c.dim == "scale":
                # 规模维度 signal 恒中性，"大盘"按 headline 判定（超大盘/大盘）
                ok = bool(dim and c.want == "bullish" and "大盘" in dim.headline)
            else:
                ok = bool(dim and dim.signal == c.want)
            (hit_labels if ok else miss_labels).append(c.label)
        return StockScreenMatch(
            symbol=sym,
            name=gname or sym,
            overall_verdict=ts.overall_verdict,
            overall_score=ts.overall_score,
            matched_all=(len(hit_labels) == len(criteria)),
            hit_count=len(hit_labels),
            hit_labels=hit_labels,
            miss_labels=miss_labels,
            data_quality=ts.data_quality,
        )

    results = list(await asyncio.gather(*[_screen_one(s) for s in syms]))
    results.sort(key=lambda m: (m.matched_all, m.hit_count, m.overall_score), reverse=True)
    levels = [r.data_quality.level for r in results]
    worst = "mock" if "mock" in levels else ("degraded" if "degraded" in levels else "live")
    overall_dq = next(r.data_quality for r in results if r.data_quality.level == worst)

    return StockScreenResponse(
        generated_at=datetime.now(timezone.utc),
        query=q,
        criteria_summary=parsed.get("summary", ""),
        criteria=criteria,
        matches=results,
        scanned=len(results),
        provider=llm.provider,
        data_quality=overall_dq,
    )


@app.get("/api/official-news/cctv", response_model=OfficialNewsResponse)
async def official_cctv_news(
    source: str = "xinwenlianbo",
    limit: int = 30,
    refresh: bool = False,
) -> OfficialNewsResponse:
    return await fetch_official_news(source=source, limit=limit, refresh=refresh)


@app.get("/api/people/spotlight", response_model=PeopleSpotlightResponse)
async def people_spotlight(refresh: bool = False) -> PeopleSpotlightResponse:
    """人物专题：焦点人物头像墙 + 各自近期发言 / 观点 / 来源。"""
    return await fetch_people_spotlight(refresh=refresh)


@app.get("/api/people/{figure_id}/digest", response_model=PersonDigestResponse)
async def people_digest(figure_id: str, refresh: bool = False) -> PersonDigestResponse:
    """单人物 AI 近期观点综述：把近期发言合成 2-3 句，方向不可编造，失败回退确定性模板。"""
    if figure_id not in FIGURES_BY_ID:
        allowed = " / ".join(FIGURES_BY_ID)
        raise HTTPException(
            status_code=404,
            detail=f"未知焦点人物：{figure_id}，可选 {allowed}",
        )
    profile = await fetch_person_voices(figure_id, refresh=refresh)
    digest, provider = await _synthesize_person_digest(profile, refresh=refresh)
    quality = profile.data_quality
    if profile.item_count and provider in {"template", "fallback"}:
        # 有真实条目但只能用确定性模板综述时，标降级而非 live。
        quality = classify_data_quality("template")
    return PersonDigestResponse(
        id=profile.id,
        name=profile.name,
        digest=digest,
        digest_provider=provider,
        item_count=profile.item_count,
        generated_at=datetime.now(timezone.utc).isoformat(),
        data_quality=quality,
    )


_digest_cache: dict[str, tuple[str, str]] = {}
_DIGEST_CACHE_MAX = 200


async def _synthesize_person_digest(profile: PersonProfile, *, refresh: bool = False) -> tuple[str, str]:
    """把人物近期发言合成一段中性观点综述：LLM 优先，mock/失败回退确定性模板。

    成功的 AI 综述按「人物 + 标题哈希」缓存：同一批标题重复打开秒回、不再耗 token；
    新发言→哈希变→自动重算。模板兜底不入缓存，保证 LLM 恢复后下次即用真综述。
    """
    headlines = [item.title for item in profile.items[:8] if item.title]
    if not headlines:
        return ("近期暂无可聚合的公开报道，请稍后刷新或查看下方原始条目。", "template")

    cache_key = digest_cache_key(profile)
    if not refresh:
        cached = _digest_cache.get(cache_key)
        if cached:
            return cached

    fallback = _template_person_digest(profile, headlines)
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        return (fallback, "template")

    bullets = "\n".join(f"- {title}" for title in headlines)
    prompt = (
        f"你是财经媒体编辑。下面是关于「{profile.name}（{profile.role}，{profile.org}）」"
        f"近期的公开报道标题，请据此客观归纳其近期关注焦点与公开观点，供投研参考。\n\n"
        f"近期报道：\n{bullets}\n\n"
        "要求：用中文写 2-3 句综述，只能基于上面出现的事实归纳、不得编造数字或未提及的表态，"
        "点出 1-2 个对市场可能的影响方向，不写免责声明、不超过 120 个中文字。"
        '仅返回 JSON：{"digest": "..."}'
    )
    try:
        data = await llm.complete_json(
            prompt,
            max_tokens=600,
            timeout_seconds=14,
            force_json_first=True,
            retry_schema_hint="只需填充 digest 一个字段，2-3 句、不超过 120 字。",
        )
    except Exception:
        return (fallback, "template")
    digest = (data or {}).get("digest")
    if isinstance(digest, str) and digest.strip():
        result = (digest.strip(), llm.provider_name)
        # 仅缓存真实 AI 综述（模板兜底不缓存，便于 LLM 恢复后自愈）。
        _digest_cache[cache_key] = result
        if len(_digest_cache) > _DIGEST_CACHE_MAX:
            _digest_cache.pop(next(iter(_digest_cache)))
        return result
    return (fallback, "template")


def _template_person_digest(profile: PersonProfile, headlines: list[str]) -> str:
    """确定性兜底综述：从近期条目的主题标签 + 最新一条提炼，不依赖云端模型。"""
    tag_counts: dict[str, int] = {}
    for item in profile.items:
        for tag in item.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = [tag for tag, _ in sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:3]]
    focus = "、".join(top_tags) if top_tags else "多条公开动态"
    latest = headlines[0] if headlines else ""
    return (
        f"近{profile.item_count}条公开报道显示，{profile.name}近期焦点集中在{focus}。"
        f"最新一条：{latest}。{profile.why_it_matters}"
    )


@app.get("/api/ai-supply-chain/capacity-trends")
async def ai_supply_chain_capacity_trends(horizon: str = "3m") -> dict[str, Any]:
    return await fetch_ai_supply_chain_capacity_trends(horizon=horizon)


@app.get("/api/customs-trade/snapshot")
async def customs_trade_snapshot() -> dict[str, Any]:
    return await fetch_customs_trade_snapshot()


@app.get("/api/customs-trade/hs-detail/search")
async def customs_trade_hs_detail_search(q: str = "", limit: int = 20) -> dict[str, Any]:
    return await search_customs_hs_detail_products(q, limit=limit)


@app.get("/api/customs-trade/hs-detail")
async def customs_trade_hs_detail(
    query: Optional[str] = None,
    code: Optional[str] = None,
    months: int = 12,
) -> dict[str, Any]:
    return await fetch_customs_hs_detail_snapshot(query=query, code=code, months=months)


async def _wait_for_customs_agent_task(task_id: str, *, timeout_seconds: float) -> InvestmentTaskRecord:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    while datetime.now(timezone.utc).timestamp() < deadline:
        task = get_investment_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Agent task not found")
        if task.status == "completed":
            return task
        if task.status in {"failed", "cancelled"}:
            detail = task.error or (task.logs[-1].message if task.logs else "Agent task failed")
            raise HTTPException(status_code=502, detail=detail)
        await asyncio.sleep(1.0)
    raise HTTPException(status_code=504, detail=f"海关投研任务 {task_id} 尚未在限定时间内完成，请到投研任务中心查看进度。")


def _customs_agent_task_to_fingpt(task: InvestmentTaskRecord) -> FinGptTaskResponse:
    result = task.result or {}
    findings = result.get("agent_findings") if isinstance(result.get("agent_findings"), dict) else {}
    evidence_sources = [
        str(item.get("title") or item.get("source"))
        for item in result.get("evidence", [])
        if isinstance(item, dict) and (item.get("title") or item.get("source"))
    ]
    sources = [
        f"DeepFocus Agent Task {task.id}",
        *evidence_sources,
    ]
    return FinGptTaskResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        capability="customs_trade_agent_runtime",
        title="中国海关进出口投研Agent",
        summary=str(result.get("investor_summary") or result.get("plain_language_takeaway") or "海关投研 Agent 已完成。"),
        key_points=_json_list(findings.get("research")) or _json_list(result.get("watchlist"))[:6],
        signals=_json_list(findings.get("report")) or _json_list(result.get("action_plan"))[:6],
        risks=_json_list(result.get("risk_controls")) or _json_list(findings.get("risk")),
        actions=_json_list(result.get("action_plan")) or _json_list(findings.get("report")),
        sources=sources[:8],
        confidence=clamp(float(result.get("confidence") or 0.6), 0, 1),
        disclaimer=str(result.get("disclaimer") or "仅供投研和运营参考，不构成投资建议、支付建议或合规结论。"),
    )


@app.post("/api/customs-trade/ai-analysis", response_model=FinGptTaskResponse)
async def customs_trade_ai_analysis(request: CustomsTradeAnalysisRequest) -> FinGptTaskResponse:
    try:
        tab_labels = {
            "chapters": "HS2商品结构",
            "exports": "重点进出口商品",
            "partners": "贸易伙伴结构",
            "hs": "HS大类结构",
            "fine": "HS明细商品",
        }
        focus = request.focus or tab_labels.get(request.selected_tab or "", request.selected_tab) or "全局海关进出口快照"
        if not is_worker_running():
            await start_agent_worker()
        task = create_investment_task(
            InvestmentTaskCreateRequest(
                title="中国海关进出口投研Agent分析",
                symbol="CUSTOMS_CN",
                asset_name="中国海关进出口",
                task_type="customs_trade_analysis",
                engine="deepfocus",
                horizon="最近12个月",
                investor_profile="专业",
                objective=f"基于中国海关进出口官方数据生成投资建议、代表股票、风险和触发条件。当前焦点：{focus}",
                context="海关总署进出口月度快照、HS2商品、重点进出口商品、贸易伙伴和近12个月曲线。",
                analysis_domain="customs_trade",
                customs_focus=focus,
                customs_focus_key=request.focus_key,
                customs_focus_type=request.focus_type,
                customs_selected_tab=request.selected_tab,
                engine_config={
                    "source": "customs_trade_module",
                    "focus_type": request.focus_type,
                },
                priority=1,
            )
        )
        completed = await _wait_for_customs_agent_task(task.id, timeout_seconds=95)
        return _customs_agent_task_to_fingpt(completed)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/skills/shareholder-changes/scan", response_model=ShareholderChangeScanResponse)
async def shareholder_change_scan(request: ShareholderChangeScanRequest) -> ShareholderChangeScanResponse:
    return await scan_shareholder_changes(request)


@app.post("/api/skills/shareholder-changes/interpret", response_model=ShareholderChangeInterpretResponse)
async def shareholder_change_interpret(request: ShareholderChangeInterpretRequest) -> ShareholderChangeInterpretResponse:
    return await interpret_shareholder_change(request, llm)


@app.post("/api/skills/cn-earnings/scan", response_model=CnEarningsScanResponse)
async def cn_earnings_scan(request: CnEarningsScanRequest) -> CnEarningsScanResponse:
    return await scan_cn_earnings(request)


@app.post("/api/skills/cn-earnings/diagnose", response_model=CnEarningsDiagnosisResponse)
async def cn_earnings_diagnose(request: CnEarningsDiagnosisRequest) -> CnEarningsDiagnosisResponse:
    return await diagnose_cn_earnings(request, llm)


@app.post("/api/skills/cn-earnings/detail", response_model=CnEarningsRecordDetailResponse)
async def cn_earnings_record_detail(request: CnEarningsRecordDetailRequest) -> CnEarningsRecordDetailResponse:
    return await enrich_cn_earnings_record_detail(request)


@app.post("/api/skills/major-events/scan", response_model=MajorEventScanResponse)
async def major_event_scan(request: MajorEventScanRequest) -> MajorEventScanResponse:
    return await scan_major_events(request)


@app.post("/api/fingpt/files/extract", response_model=FileExtractionResponse)
async def extract_file(file: UploadFile = File(...)) -> FileExtractionResponse:
    return await extract_upload_file(file)


@app.get("/api/data-sources", response_model=DataSourceListResponse)
async def api_list_data_sources() -> DataSourceListResponse:
    return DataSourceListResponse(sources=list_data_sources())


@app.post("/api/data-sources", response_model=DataSourceRecord)
async def api_create_data_source(request: DataSourceCreateRequest) -> DataSourceRecord:
    return create_data_source(request)


@app.get("/api/data-sources/module-refs", response_model=DataSourceModuleRefListResponse)
async def api_list_data_source_module_refs(
    source_id: Optional[str] = None,
    module: Optional[str] = None,
) -> DataSourceModuleRefListResponse:
    return DataSourceModuleRefListResponse(refs=list_data_source_module_refs(source_id=source_id, module=module))


@app.post("/api/data-sources/module-refs", response_model=DataSourceModuleRefRecord)
async def api_save_data_source_module_ref(request: DataSourceModuleRefCreateRequest) -> DataSourceModuleRefRecord:
    return save_data_source_module_ref(request)


@app.delete("/api/data-sources/module-refs/{ref_id}")
async def api_delete_data_source_module_ref(ref_id: str) -> dict[str, bool]:
    if not delete_data_source_module_ref(ref_id):
        raise HTTPException(status_code=404, detail="Data source reference not found")
    return {"ok": True}


@app.delete("/api/data-sources/{source_id}")
async def api_delete_data_source(source_id: str) -> dict[str, bool]:
    if not delete_data_source(source_id):
        raise HTTPException(status_code=404, detail="Data source not found")
    return {"ok": True}


@app.post("/api/data-sources/{source_id}/sync", response_model=DataSourceSyncResponse)
async def api_sync_data_source(source_id: str, request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await sync_data_source(source_id, request)
    publish_data_source_items(items, topic="data-source-sync")
    return DataSourceSyncResponse(source=source, imported_count=len(items), items=items)


@app.get("/api/data-sources/items", response_model=DataSourceItemListResponse)
async def api_list_data_items(
    symbol: Optional[str] = None,
    query: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    sort: str = "time_desc",
) -> DataSourceItemListResponse:
    return DataSourceItemListResponse(
        items=list_data_items(
            symbol=symbol,
            query=query,
            source_type=source_type,
            source_id=source_id,
            tag=tag,
            limit=max(1, min(limit, 100)),
            sort=sort,
        )
    )


@app.get("/api/data-sources/items/tags", response_model=DataSourceTagListResponse)
async def api_list_data_tags() -> DataSourceTagListResponse:
    return DataSourceTagListResponse(tags=list_data_tags())


@app.get("/api/data-sources/corpus-stats", response_model=DataSourceCorpusStats)
async def api_corpus_stats() -> DataSourceCorpusStats:
    """证据语料的质量与覆盖聚合（全量扫描）——证据库「语料质量与覆盖」概览。"""
    return corpus_stats()


@app.get("/api/data-sources/items/{item_id}", response_model=DataSourceItemRecord)
async def api_get_data_item(item_id: str) -> DataSourceItemRecord:
    item = get_data_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    return item


@app.patch("/api/data-sources/items/{item_id}", response_model=DataSourceItemRecord)
async def api_update_data_item(item_id: str, request: DataSourceItemUpdateRequest) -> DataSourceItemRecord:
    item = update_data_item(item_id, request)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    return item


@app.post("/api/data-sources/items/{item_id}/interpret", response_model=DataSourceItemInterpretResponse)
async def api_interpret_data_item(
    item_id: str,
    request: DataSourceItemInterpretRequest = DataSourceItemInterpretRequest(),
) -> DataSourceItemInterpretResponse:
    item = get_data_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    if llm.provider == "mock":
        raise HTTPException(
            status_code=409,
            detail="当前模型仍是 mock 模式，不能执行真实 AI 解读。请先在 FinGPT → 模型配置 中配置 OpenAI、Minimax 或 OpenAI-compatible 的 API Key。",
        )

    try:
        if _is_wechat_public_item(item):
            result = await llm.analyze_wechat_article(_wechat_article_payload(item))
        else:
            result = await llm.analyze_report(
                ReportAnalysisRequest(
                    title=item.title,
                    report_text=item.text,
                    locale="zh-CN",
                )
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 解读失败：{exc}") from exc
    interpretation = _format_interpretation(result)
    updated = item
    if request.persist:
        updated = update_data_item(item.id, DataSourceItemUpdateRequest(ai_interpretation=interpretation)) or item
    return DataSourceItemInterpretResponse(item=updated, interpretation=interpretation, result=result)


@app.delete("/api/data-sources/items/{item_id}")
async def api_delete_data_item(item_id: str) -> dict[str, bool]:
    if not delete_data_item(item_id):
        raise HTTPException(status_code=404, detail="Data item not found")
    return {"ok": True}


@app.post("/api/data-sources/upload", response_model=DataSourceItemRecord)
async def api_upload_data_file(
    file: UploadFile = File(...),
    symbol: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
) -> DataSourceItemRecord:
    _reject_non_ingestible_file(file.filename or "")
    extracted = await extract_upload_file(file)
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=symbol,
        title=title,
        tags=parsed_tags,
    )
    publish_data_source_items([item], topic="data-source-upload", severity="success")
    return item


@app.post("/api/pro-research/reports/upload", response_model=ProfessionalReportRecord)
async def api_upload_professional_report(
    file: UploadFile = File(...),
    symbol: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    report_type: str = Form("other"),
    period: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
) -> ProfessionalReportRecord:
    _reject_non_ingestible_file(file.filename or "")
    extracted = await extract_upload_file(file)
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=symbol,
        title=title,
        tags=[*parsed_tags, "专业财报库"],
    )
    publish_data_source_items([item], topic="pro-research-upload", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=title or extracted.filename,
        symbol=symbol,
        report_type=report_type,
        period=period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "data_item_id": item.id,
            "tags": [*parsed_tags, "专业财报库"],
        },
    )


@app.post("/api/pro-research/reports/ingest-item", response_model=ProfessionalReportRecord)
async def api_ingest_professional_report_item(
    request: ProfessionalReportIngestRequest,
) -> ProfessionalReportRecord:
    return ingest_professional_report_from_item(request)


@app.post("/api/pro-research/reports/ingest-url", response_model=ProfessionalReportRecord)
async def api_ingest_professional_report_url(
    request: ProfessionalReportUrlIngestRequest,
) -> ProfessionalReportRecord:
    extracted = await extract_report_url(request.url)
    tags = list(dict.fromkeys([*request.tags, "URL入库", "专业财报库"]))
    title = request.title or extracted.title
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=request.symbol,
        title=title,
        tags=tags,
        url=extracted.final_url,
        metadata={
            "source_url": extracted.url,
            "final_url": extracted.final_url,
            "parser": extracted.parser,
            "truncated": extracted.truncated,
            "tags": tags,
        },
    )
    publish_data_source_items([item], topic="pro-research-url-ingest", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=title,
        symbol=request.symbol,
        report_type=request.report_type,
        period=request.period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "source_url": extracted.url,
            "final_url": extracted.final_url,
            "data_item_id": item.id,
            "tags": tags,
            "truncated": extracted.truncated,
        },
    )


_JUNK_FILENAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
_INGESTIBLE_SUFFIXES = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown",
    ".html", ".htm", ".csv", ".xlsx", ".xls", ".pptx", ".rtf", ".json",
}


def _reject_non_ingestible_file(filename: str) -> None:
    """拒绝系统/隐藏/垃圾文件与不支持的类型，避免 .DS_Store 之类被切块入 RAG。"""
    base = Path((filename or "").strip()).name
    if not base or base.lower() in _JUNK_FILENAMES or base.startswith("."):
        raise HTTPException(status_code=422, detail=f"该文件疑似系统/隐藏文件（{base or '空文件名'}），不是可入库的研报。")
    suffix = Path(base).suffix.lower()
    if suffix and suffix not in _INGESTIBLE_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"暂不支持的研报文件类型：{suffix}。支持 PDF/Word/Excel/PPT/TXT/Markdown/HTML/CSV。")


@app.post("/api/pro-research/reports/ingest-workbench-file", response_model=ProfessionalReportRecord)
async def api_ingest_professional_workbench_file(
    request: ProfessionalWorkbenchFileIngestRequest,
) -> ProfessionalReportRecord:
    _reject_non_ingestible_file(request.filename)
    file_path = _safe_workbench_file_path(request.out, request.filename)
    extracted = extract_local_file(file_path)
    parsed_tags = [tag.strip() for tag in request.tags if tag.strip()]
    tags = [*parsed_tags, "抓取舱", "专业财报库"]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=request.symbol,
        title=request.title or file_path.stem,
        tags=tags,
    )
    publish_data_source_items([item], topic="pro-research-workbench-ingest", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=request.title or file_path.stem,
        symbol=request.symbol,
        report_type=request.report_type,
        period=request.period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "workbench_out": request.out,
            "workbench_path": str(file_path),
            "data_item_id": item.id,
            "tags": tags,
        },
    )


@app.get("/api/pro-research/reports", response_model=ProfessionalReportListResponse)
async def api_list_professional_reports(
    symbol: Optional[str] = None,
    limit: int = 50,
) -> ProfessionalReportListResponse:
    return ProfessionalReportListResponse(reports=list_professional_reports(symbol=symbol, limit=limit))


@app.get("/api/pro-research/reports/{report_id}", response_model=ProfessionalReportRecord)
async def api_get_professional_report(report_id: str) -> ProfessionalReportRecord:
    report = get_professional_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Professional report not found")
    return report


@app.get("/api/pro-research/reports/{report_id}/chunks", response_model=ProfessionalReportChunkListResponse)
async def api_list_professional_report_chunks(
    report_id: str,
    limit: int = 100,
) -> ProfessionalReportChunkListResponse:
    if not get_professional_report(report_id):
        raise HTTPException(status_code=404, detail="Professional report not found")
    return ProfessionalReportChunkListResponse(chunks=list_professional_chunks(report_id, limit=limit))


@app.get("/api/pro-research/metrics", response_model=ProfessionalMetricListResponse)
async def api_list_professional_metrics(
    report_id: Optional[str] = None,
    symbol: Optional[str] = None,
    metric_key: Optional[str] = None,
    limit: int = 100,
) -> ProfessionalMetricListResponse:
    return ProfessionalMetricListResponse(
        metrics=list_professional_metrics(
            report_id=report_id,
            symbol=symbol,
            metric_key=metric_key,
            limit=limit,
        )
    )


@app.post("/api/pro-research/rag/query", response_model=ProfessionalRagQueryResponse)
async def api_professional_rag_query(
    request: ProfessionalRagQueryRequest,
) -> ProfessionalRagQueryResponse:
    return await query_professional_rag(request)


@app.post("/api/pro-research/reports/{report_id}/analyze", response_model=ProfessionalReportAnalysisResponse)
async def api_analyze_professional_report(
    report_id: str,
    request: ProfessionalReportAnalysisRequest = ProfessionalReportAnalysisRequest(),
) -> ProfessionalReportAnalysisResponse:
    return await analyze_professional_report(report_id, request)


@app.post("/api/pro-research/evals/run", response_model=ProfessionalEvalRunResponse)
async def api_run_professional_eval(
    request: ProfessionalEvalRunRequest,
) -> ProfessionalEvalRunResponse:
    return await run_professional_eval(request)


_RESEARCH_INFO_CODE_RE = re.compile(r"[A-Za-z0-9]{1,64}")
_RESEARCH_PDF_TIMEOUT = httpx.Timeout(20.0, connect=6.0)


_ZSXQ_HEALTH: dict[str, Any] = {"ok": True, "last_ok": "", "last_check": "", "fails": 0, "detail": "", "alerted": False}
_WECHAT_HEALTH: dict[str, Any] = {"ok": True, "last_ok": "", "last_check": "", "fails": 0, "detail": "", "alerted": False}


async def run_wechat_health() -> None:
    """个人微信桥接(gewechat)登录态健康监测：定期探活，掉线即记录 + 看板红灯 + 邮件告警提醒重新扫码（恢复后再次邮件）。

    未配置微信桥接 → 安静空转（不告警），与邮件/Web Push 同「未配置即优雅跳过」约定。
    """
    interval = float(os.getenv("DEEPFOCUS_WECHAT_PROBE_SECONDS", "300"))
    await asyncio.sleep(50)
    print(f"[wechat-health] 启动：每 {interval}s 探活一次")
    while True:
        try:
            ok, detail = await asyncio.to_thread(probe_wechat_online)
            now = datetime.now(timezone.utc).isoformat()
            # 未配置：不算故障，安静空转
            if "未配置" in detail or "缺 appId" in detail or "依赖不可用" in detail:
                _WECHAT_HEALTH.update({"ok": True, "last_check": now, "detail": detail, "fails": 0})
                await asyncio.sleep(interval)
                continue
            _WECHAT_HEALTH["last_check"] = now
            _WECHAT_HEALTH["detail"] = detail
            if ok:
                _WECHAT_HEALTH["last_ok"] = now
                _WECHAT_HEALTH["fails"] = 0
                _WECHAT_HEALTH["ok"] = True
                if _WECHAT_HEALTH.get("alerted"):  # 之前告过警，现已恢复 → 发恢复通知
                    _WECHAT_HEALTH["alerted"] = False
                    try:
                        await asyncio.to_thread(
                            send_alert_email, "✅ 微信推送已恢复",
                            f"个人微信桥接登录态已恢复在线，信号推送恢复正常。\n时间：{now}",
                        )
                    except Exception:  # noqa: BLE001
                        pass
            else:
                _WECHAT_HEALTH["ok"] = False
                _WECHAT_HEALTH["fails"] = int(_WECHAT_HEALTH.get("fails", 0)) + 1
                # 连续 2 次失败才告警（避开偶发抖动），且只告一次直到恢复
                if _WECHAT_HEALTH["fails"] >= 2 and not _WECHAT_HEALTH.get("alerted"):
                    _WECHAT_HEALTH["alerted"] = True
                    print(f"[wechat-health] 微信掉线告警：{detail}")
                    try:
                        sent, info = await asyncio.to_thread(
                            send_alert_email,
                            "⚠️ 微信推送已掉线，请重新扫码登录",
                            "个人微信桥接(gewechat)登录态已掉线，信号推送暂停。\n\n"
                            f"状态：{detail}\n时间：{now}\n\n"
                            "处理：在跑 gewechat 的机器上重新扫码登录专用号即可恢复"
                            "（个人号掉线后 token 失效，无法自动重连，必须人工补扫）。\n"
                            "期间邮件/Web Push 通道不受影响，仍正常推送。",
                        )
                        print(f"[wechat-health] 告警邮件：{info}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[wechat-health] 告警邮件异常：{exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[wechat-health] 探活异常：{type(exc).__name__}")
        await asyncio.sleep(interval)


async def run_zsxq_health() -> None:
    """研报登录态(ZSXQ cookie)健康监测：定期探活，失效即记录 + 看板红灯 + 邮件告警（恢复后再次邮件）。"""
    interval = float(os.getenv("DEEPFOCUS_ZSXQ_PROBE_SECONDS", "300"))
    await asyncio.sleep(45)
    print(f"[zsxq-health] 启动：每 {interval}s 探活一次")
    while True:
        try:
            ok, detail = await probe_zsxq()
            now = datetime.now(timezone.utc).isoformat()
            _ZSXQ_HEALTH["last_check"] = now
            _ZSXQ_HEALTH["detail"] = detail
            if ok:
                _ZSXQ_HEALTH["last_ok"] = now
                _ZSXQ_HEALTH["fails"] = 0
                if _ZSXQ_HEALTH.get("alerted"):  # 之前告过警，现已恢复 → 发恢复通知
                    _ZSXQ_HEALTH["alerted"] = False
                    try:
                        send_alert_email("✅ 研报源已恢复", f"知识星球登录态已恢复正常。\n时间：{now}")
                    except Exception:  # noqa: BLE001
                        pass
                _ZSXQ_HEALTH["ok"] = True
            else:
                _ZSXQ_HEALTH["ok"] = False
                _ZSXQ_HEALTH["fails"] = int(_ZSXQ_HEALTH.get("fails", 0)) + 1
                # 连续 2 次失败才告警（避开偶发抖动），且只告一次直到恢复
                if _ZSXQ_HEALTH["fails"] >= 2 and not _ZSXQ_HEALTH.get("alerted"):
                    _ZSXQ_HEALTH["alerted"] = True
                    print(f"[zsxq-health] 登录态失效告警：{detail}")
                    try:
                        sent, info = send_alert_email(
                            "⚠️ 研报登录态(知识星球cookie)已失效，请更换",
                            "研报源拉取失败，cookie 可能已过期。\n\n"
                            f"错误：{detail}\n时间：{now}\n\n"
                            "处理：打开数据看板，在「研报源」处粘贴新的 cookie 一键更新即可（无需重启）。\n"
                            "期间网站仍用缓存的研报继续显示，只是新研报暂停。",
                        )
                        print(f"[zsxq-health] 告警邮件：{info}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[zsxq-health] 告警邮件异常：{exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[zsxq-health] 探活异常：{type(exc).__name__}")
        await asyncio.sleep(interval)


@app.get("/api/research/auth-status")
async def api_research_auth_status(request: Request, token: str = "") -> dict[str, Any]:
    """研报登录态健康状态（管理员，需 metrics 令牌）：是否正常、上次正常时间、是否用了热更新 cookie。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    ov = load_zsxq_override()
    return {
        **_ZSXQ_HEALTH,
        "override_active": bool(ov.get("cookie")),
        "override_updated_at": ov.get("updated_at", ""),
    }


@app.get("/api/wechat/health")
async def api_wechat_health(request: Request, token: str = "") -> dict[str, Any]:
    """微信桥接(gewechat)登录态健康状态（管理员，需 metrics 令牌）：是否在线、上次在线时间、连续失败数。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    return dict(_WECHAT_HEALTH)


@app.post("/api/research/auth")
async def api_research_auth(request: Request, token: str = "") -> dict[str, Any]:
    """一键更新研报登录态（管理员，需令牌）：粘贴新 cookie → 先探活验证 → 通过才保存、即时生效。

    body: {cookie: "...", aduid?: "...", clear?: true}"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if body.get("clear"):
        clear_zsxq_override()
        return {"ok": True, "cleared": True}
    cookie = str(body.get("cookie") or "").strip()
    aduid = str(body.get("aduid") or "").strip()
    parsed_cookie, parsed_aduid = parse_curl_cookie(cookie)  # 支持直接粘开发者工具的整段 curl
    if parsed_cookie:
        cookie = parsed_cookie
    if not aduid and parsed_aduid:
        aduid = parsed_aduid
    if len(cookie) < 10:
        raise HTTPException(status_code=400, detail="cookie 太短/为空")
    ok, detail = await probe_zsxq(cookie=cookie, aduid=aduid)  # 先验证再保存，避免存进坏 cookie
    if not ok:
        raise HTTPException(status_code=400, detail=f"该 cookie 验证未通过，未保存：{detail}")
    save_zsxq_override(cookie, aduid)
    _ZSXQ_HEALTH.update({"ok": True, "fails": 0, "alerted": False,
                         "last_ok": datetime.now(timezone.utc).isoformat(), "detail": "已更新"})
    return {"ok": True, "saved": True, "detail": "新登录态已验证并即时生效"}


@app.post("/api/research/test-email")
async def api_research_test_email(request: Request, token: str = "") -> dict[str, Any]:
    """发送一封测试告警邮件（管理员，需令牌）：验证 SMTP/收件人配置是否正常。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    sent, info = send_alert_email(
        "✅ DEEPFOCUS 看板 · 告警邮件测试",
        "这是一封测试邮件。如果你收到了，说明研报源失效告警邮件已配置成功。",
    )
    return {"ok": sent, "detail": info}


def _instruments_for(file_id: Any, cached: Any = None) -> list[str]:
    """从已缓存的 AI 解读里取该研报「提及标的」；cached 可由调用方批量预取传入，避免逐条查 SQLite。"""
    fid = str(file_id or "").strip()
    if cached is None and fid:
        cached = metrics_get_ai_cache(fid)
    if isinstance(cached, dict):
        ins = cached.get("instruments")
        if isinstance(ins, list):
            return [str(x) for x in ins if str(x).strip()][:8]
    return []


# 「强」市场信号：直接表明研报对象所在市场（市场名/交易所/公司名/代码后缀）
_MKT_US_STRONG = (
    "美股", "纳斯达克", "纳指", "标普", "道琼斯", "道指", "费城半导体", "费半", ".us",
    "英伟达", "nvidia", "nvda", "特斯拉", "tesla", "tsla", "苹果公司", "apple", "aapl", "微软", "microsoft", "msft",
    "谷歌", "google", "alphabet", "亚马逊", "amazon", "amzn", "meta", "脸书", "奈飞", "netflix",
    "博通", "broadcom", "美光", "micron", "高通", "qualcomm", "qcom", "英特尔", "intel", "amd", "台积电", "tsmc", "tsm",
    "阿斯麦", "asml", "甲骨文", "oracle", "palantir", "美超微", "supermicro", "arm", "marvell", "戴尔", "dell",
    "snowflake", "snow", "datadog", "coinbase", "lrcx", "amat", "goldman", "morgan",
    "nasdaq", "s&p", "s&p500", "dow jones",
)
_MKT_HK_STRONG = (
    "港股", "恒生", "恒指", "港交所", "h股", ".hk", "港元", "港币", "hk$", "恒生科技", "国企指数", "中国互联网",
    "腾讯", "tencent", "阿里巴巴", "alibaba", "美团", "小米集团", "快手", "中芯国际", "友邦", "汇丰", "理想汽车", "蔚来", "小鹏", "康方",
    "泡泡玛特", "popmart", "pop mart", "美图", "建滔", "华润万象", "名创优品", "miniso", "农夫山泉", "海底捞",
    "安踏", "李宁", "周大福", "网易", "netease", "百度", "baidu", "携程", "trip.com",
)
# 「弱」信号：宏观背景词，本身不决定研报市场（如某 A 股策略报告借「美联储加息」做背景）
_MKT_US_WEAK = ("美国", "美联储", "fomc", "美债", "美国国债", "降息", "加息", "非农", "华尔街", "美元指数")
_MKT_HK_WEAK = ("香港", "中概", "海外中资")
# 明确「境外其它市场」：日本/欧洲/印度等地缘宏观或市场 → 归「港美/海外」桶（用 US 返回值落到港美股标签页）
_MKT_OVERSEAS = ("日本", "日经", "nikkei", "欧洲", "欧元", "欧央行", "ecb", "德国", "英国", "法国", "印度", "越南", "韩国")
_MKT_A_KW = (
    "a股", "沪深", "沪市", "深市", "上证", "深证", "科创", "创业板", "北交所", "北证", "两市", "龙虎榜",
    "涨停", "游资", "打板", "北向", "北上资金", "主力净", "沪指", "深成指", "涨停板", "炸板", "连板",
    "四千点", "4000点", "3000点", "3500点", "中国经济", "国内",
)
_MKT_COMMO = ("黄金", "原油", "白银", "比特币")
_MKT_A_CODE = re.compile(r"\.(SH|SZ)\b|(^|\D)(60\d{4}|68\d{4}|00\d{4}|30\d{4})(\D|$)", re.I)
_MKT_HK_CODE = re.compile(r"\.HK\b|(^|\D)0\d{4}(\D|$)", re.I)
_MKT_US_CODE = re.compile(r"^[A-Za-z]{1,5}$")
_MKT_CJK = re.compile(r"[一-龥]")


def _kw_hit(text: str, kws: tuple) -> bool:
    """关键词命中：纯字母短词用词边界（避免 arm 命中 harmoni、snow 命中 snowflake 之外的词），中文/含点词用子串。"""
    for k in kws:
        if k.isascii() and k.replace(" ", "").isalpha():
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text):
                return True
        elif k in text:
            return True
    return False


def _market_for(file_id: Any, title: str = "", cached: Any = None) -> str:
    """解析研报主要市场 → 'A'/'HK'/'US'/''。优先模型判定；否则用『标题+缓存subject/摘要+标的代码』综合启发式。

    要点：①很多研报标题中性、无提取标的，但缓存 subject/摘要明确写了市场（「美国软件行业」「HSAI.US」「标普500」）；
    ②区分『强信号』(市场/交易所/公司名/代码) 与『弱信号』(美联储/美国 等宏观背景词)，避免 A 股策略报告借宏观被误判海外。"""
    if cached is None and file_id:
        cached = metrics_get_ai_cache(str(file_id or "").strip())
    insts: list = []
    subj = ""
    if isinstance(cached, dict):
        m = str(cached.get("market") or "").strip()
        if m in ("A", "HK", "US"):
            return m
        insts = cached.get("instruments") or []
        subj = " ".join(str(cached.get(k) or "") for k in ("subject", "one_liner", "summary"))
    text = f"{title} {subj} {' '.join(str(x) for x in insts)}".lower()
    # 1) 标的代码计票（最可靠）
    a = hk = us = 0
    for raw in insts:
        t = str(raw).strip()
        tl = t.lower()
        if t in _MKT_COMMO:
            continue
        if _MKT_A_CODE.search(t):
            a += 1
        elif _MKT_HK_CODE.search(t):
            hk += 1
        elif _MKT_US_CODE.match(t):
            us += 1
        elif _MKT_CJK.search(t):
            if _kw_hit(tl, _MKT_US_STRONG):
                us += 1
            elif _kw_hit(tl, _MKT_HK_STRONG):
                hk += 1
            else:
                a += 1
    overseas = hk + us
    if a != overseas:
        if overseas <= a:
            return "A"
        return "US" if us >= hk else "HK"
    # 2) 代码无定论 → 文本强/弱信号
    us_s, hk_s = _kw_hit(text, _MKT_US_STRONG), _kw_hit(text, _MKT_HK_STRONG)
    ovs = _kw_hit(text, _MKT_OVERSEAS)   # 日本/欧洲/印度… 明确境外 → 归海外（用 US 桶=港美股标签页）
    us_like = us_s or ovs
    us_w, hk_w = _kw_hit(text, _MKT_US_WEAK), _kw_hit(text, _MKT_HK_WEAK)
    # 「A 股」常被排版成带空格（"A 股"）→ 去空格再判，避免漏判
    a_kw = _kw_hit(text, _MKT_A_KW) or ("a股" in text.replace(" ", "").replace("　", ""))
    strong = us_like or hk_s
    if strong and not a_kw:
        return "HK" if (hk_s and not us_like) else "US"
    if a_kw and not strong:
        return "A"                       # 仅弱海外背景 + 明确A信号 → A股（如「美联储加息下的A股」）
    if strong:                           # 强海外 + 也有A信号：强信号优先海外
        return "US" if us_like else "HK"
    if a_kw:
        return "A"
    if us_w or hk_w:                      # 只有弱海外信号、无A（如「美国经济」）→ 海外
        return "US" if us_w else "HK"
    return "A" if (insts or subj) else ""


_WIRE_RESP_CACHE: dict[str, tuple[float, ResearchWireResponse]] = {}


def _wire_etag(resp: "ResearchWireResponse") -> str:
    """内容指纹：列表条目 id + 总数。列表没变 → ETag 不变 → 浏览器拿 304（0 流量）。"""
    import hashlib as _h
    raw = "|".join(str(it.id) for it in resp.items) + f"#{resp.total}#{resp.source}"
    return _h.md5(raw.encode("utf-8")).hexdigest()[:20]


def _wire_conditional(request: Request, resp: "ResearchWireResponse"):
    """304 协商：If-None-Match 命中指纹 → 304；否则带 ETag 返回完整响应。
    注意 nginx gzip 会把强 ETag 转成弱 W/"..."，比较前剥掉 W/ 与引号。"""
    etag = _wire_etag(resp)
    inm = (request.headers.get("if-none-match") or "").strip()
    if inm.startswith("W/"):
        inm = inm[2:]
    if inm.strip('"') == etag:
        return Response(status_code=304, headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"})
    return JSONResponse(
        content=resp.model_dump(mode="json"),
        headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"},
    )
_WIRE_RESP_TTL = float(os.getenv("DEEPFOCUS_WIRE_RESP_TTL", "45"))  # 已构建响应(含市场分类CPU)缓存秒数


@app.get("/api/research/wire", response_model=ResearchWireResponse)
async def api_research_wire(request: Request, limit: int = 60, q: str = ""):
    """知识星球「海外投行报告」研报流：终端「研报」面板的数据源。

    优先**在线**（同机 Node 工作台直连知识星球：空 q=最新、带 q=搜索），
    在线不可用时回退本地抓取舱。每条带 preview_url，可在终端内联预览原文 PDF。"""
    # 响应级缓存：富化(市场分类/标的)有 CPU 成本，几十并发下重复请求同一列表直接命中，避免重复计算
    _rk = f"{q.strip()}|{limit}"
    _hit = _WIRE_RESP_CACHE.get(_rk)
    if _hit and (time.monotonic() - _hit[0]) < _WIRE_RESP_TTL:
        return _wire_conditional(request, _hit[1])
    try:
        online = await fetch_research_wire_online(limit=limit, query=q)
        if online["items"]:
            # 一次性批量取所有研报的 AI 缓存（替代逐条 2 次 SQLite 读，几十并发下从 ~1.6s 降到几十 ms）
            cache_map = metrics_get_ai_cache_many([row["file_id"] for row in online["items"] if row.get("file_id")])
            online_items = [
                ResearchWireItem(
                    id=row["id"], title=row["title"], org=row["org"], date=row["date"],
                    created_at=row["created_at"], filename=row["filename"], out=row["out"],
                    size=row["size"], hashtag=row["hashtag"], download_count=row["download_count"],
                    file_id=row["file_id"],
                    instruments=_instruments_for(row["file_id"], cache_map.get(str(row.get("file_id") or "").strip())),
                    market=_market_for(row["file_id"], row["title"], cache_map.get(str(row.get("file_id") or "").strip())),
                    preview_url=(
                        "/api/research/wire-file"
                        f"?file_id={quote(row['file_id'])}&name={quote(row['filename'])}"
                    ),
                )
                for row in online["items"]
            ]
            _resp = ResearchWireResponse(
                items=online_items, total=online["total"],
                source="海外投行研报 · 在线检索",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                data_quality=DataQuality(
                    level="live", label="在线 · 海外投行",
                    detail=(f"实时检索「{q.strip()}」· {online['total']} 篇" if q.strip()
                            else f"实时最新 · {online['total']} 篇"),
                ),
            )
            _WIRE_RESP_CACHE[_rk] = (time.monotonic(), _resp)
            if len(_WIRE_RESP_CACHE) > 80:  # 防无界增长
                _WIRE_RESP_CACHE.pop(next(iter(_WIRE_RESP_CACHE)), None)
            return _wire_conditional(request, _resp)
    except Exception:  # 在线失败（工作台未起/cookie 失效/网络）→ 回退本地抓取舱
        pass

    result = list_research_wire(limit=max(1, min(limit, 200)), query=q)
    items = [
        ResearchWireItem(
            id=row["id"],
            title=row["title"],
            org=row["org"],
            date=row["date"],
            created_at=row["created_at"],
            filename=row["filename"],
            out=row["out"],
            size=row["size"],
            hashtag=row["hashtag"],
            download_count=row["download_count"],
            preview_url=(
                "/api/research/workbench-pdf"
                f"?filename={quote(row['filename'])}&out={quote(row['out'])}"
            ),
        )
        for row in result["items"]
    ]

    if not result["exists"]:
        data_quality = DataQuality(
            level="degraded", label="研报库未同步",
            detail="研报库暂未就绪，请稍后重试或在工作台同步",
            reasons=["workbench-empty"],
        )
    elif result["total"] == 0:
        data_quality = DataQuality(
            level="degraded", label="暂无研报",
            detail="抓取舱内暂无匹配研报", reasons=["no-report"],
        )
    else:
        data_quality = DataQuality(
            level="live", label="海外投行研报",
            detail=f"海外投行报告 · 已入库 {result['total']} 篇",
        )

    return _wire_conditional(request, ResearchWireResponse(
        items=items,
        total=result["total"],
        source="海外投行研报",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        data_quality=data_quality,
    ))


@app.get("/api/research/workbench-pdf")
async def api_research_workbench_pdf(
    filename: str, out: str = "downloads/海外投行报告"
) -> FileResponse:
    """内联返回抓取舱内的研报原文（终端研报面板预览）。

    路径穿越由 _safe_workbench_file_path 防护；Content-Disposition 用 ASCII 文件名，
    避免中文研报名破坏响应头编码。

    ⚠️原文文件下载默认关闭（DEEPFOCUS_RESEARCH_FILE_DOWNLOAD!=1 → 403）：第三方研报版权 +
    不对用户开放任何原始文件，只提供 AI 解读（服务端读文件，不经此端点）。"""
    if os.getenv("DEEPFOCUS_RESEARCH_FILE_DOWNLOAD", "0") != "1":
        raise HTTPException(status_code=403, detail="研报原文下载未开放，请使用 AI 解读")
    path = _safe_workbench_file_path(out, filename)
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
    metrics_incr_research(filename, filename)  # 研报下载/打开计数（本地原文）
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="report{path.suffix.lower()}"',
            "Cache-Control": "public, max-age=3600",
        },
    )


async def _fetch_research_online_pdf(file_id: str, name: str = "") -> tuple[bytes, str]:
    """经同机 Node 工作台解析研报在线下载链并取回原文字节，返回 (content, content_type)。

    供在线预览（wire-file）与在线研报 AI 解读（vision-analyze）共用。"""
    base = f"http://127.0.0.1:{os.getenv('RESEARCH_WORKBENCH_INTERNAL_PORT', '3927')}"
    safe_name = (name or f"{file_id}.pdf").strip()
    async with httpx.AsyncClient(trust_env=False) as client:
        pr = await client.post(
            f"{base}/api/preview", json={"fileId": file_id, "name": safe_name, **zsxq_auth_payload()}, timeout=30,
        )
        pr.raise_for_status()
        preview_url = (pr.json() or {}).get("previewUrl")
        if not preview_url:
            raise HTTPException(status_code=502, detail="工作台未返回在线预览地址")
        fr = await client.get(f"{base}{preview_url}", timeout=90)
        fr.raise_for_status()
        return fr.content, (fr.headers.get("content-type") or "application/pdf")


@app.get("/api/research/wire-file")
async def api_research_wire_file(file_id: str, name: str = "") -> Response:
    """在线预览研报原文：经同机 Node 工作台解析在线下载链并流式返回。

    用于终端研报面板「在线」模式下点开原文（本地未下载也能读）。

    ⚠️原文文件下载默认关闭（DEEPFOCUS_RESEARCH_FILE_DOWNLOAD!=1 → 403）：不对用户开放原始文件，
    只提供 AI 解读（vision-analyze 经 _fetch_research_online_pdf 服务端读取，不经此端点）。"""
    if os.getenv("DEEPFOCUS_RESEARCH_FILE_DOWNLOAD", "0") != "1":
        raise HTTPException(status_code=403, detail="研报原文下载未开放，请使用 AI 解读")
    safe_name = (name or f"{file_id}.pdf").strip()
    ext = safe_name[safe_name.rfind("."):].lower() if "." in safe_name else ".pdf"
    try:
        content, content_type = await _fetch_research_online_pdf(file_id, safe_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"在线预览失败：{str(exc)[:80]}")
    metrics_incr_research(file_id, safe_name)  # 研报下载/打开计数
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="report{ext}"',
            "Cache-Control": "no-store",
        },
    )


_MOBILE_UA_RE = re.compile(r"(Mobile|Android|iPhone|iPad|iPod|Windows Phone|HarmonyOS|MicroMessenger)", re.IGNORECASE)


@app.post("/api/metrics/pageview")
async def api_metrics_pageview(request: Request) -> dict[str, Any]:
    """记录一次页面访问（前端加载时调用一次）。附带设备类型与时段分布，不回传总量。"""
    metrics_incr("pageview")
    ua = request.headers.get("user-agent") or ""
    metrics_incr("pv_mobile" if _MOBILE_UA_RE.search(ua) else "pv_desktop")
    try:
        metrics_incr_hourly(datetime.now(timezone.utc).astimezone().hour)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


_METRICS_ALLOWED_EVENTS = {"copy_text", "copy_image", "copy_news", "brief"}


@app.post("/api/metrics/event")
async def api_metrics_event(name: str = "", title: str = "") -> dict[str, Any]:
    """前端埋点（仅白名单事件，防刷计数器）。用于统计复制等交互次数 + 单条快讯热度。"""
    key = (name or "").strip()
    if key not in _METRICS_ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="不支持的事件")
    metrics_incr(key)
    if key == "copy_news" and title.strip():
        import hashlib as _hl
        ref = "kx:" + _hl.sha1(title.strip().encode("utf-8")).hexdigest()[:16]
        metrics_incr_news_heat(ref, title.strip())
    return {"ok": True}


_ACTIVITY_ACTIONS = {
    "pageview", "login", "logout", "open_report", "ai_report", "ai_news",
    "copy", "open_pdf", "download", "search", "tab", "open_news",
    # 增长/变现漏斗动作（前端已埋点；open_buy=打开购买页，buy_contact=点「我已付款」）
    "invite_click", "claim_trial", "open_buy", "buy_contact",
    # 购买弹窗中间漏斗（定位 open_buy→buy_contact 流失在哪一步）+ 注册成功
    "buy_pkg_select", "buy_qr_view", "buy_close", "signup",
    "ai_chat",  # AI 投研问答提问（内测）
}
# 记录策略：不再用固定白名单逐个枚举（漏一个就丢一个）。改为「格式合法即记」——
# 任何 [a-z][a-z0-9_]{1,39} 的动作名都落库，覆盖全部现有+未来埋点；脏串/超长串拒掉防滥用。
import re as _re_act
_ACTIVITY_ACTION_RE = _re_act.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _resolve_actor(request: Request, session: str = "") -> tuple[str, str, str]:
    """解析操作者 → (kind, id, name)。有有效 JWT=登录账号；否则=匿名访客(按前端会话id+IP)。"""
    claims = current_claims(request)
    if claims and claims.get("sub"):
        name = str(claims.get("username") or claims.get("email") or claims.get("sub"))
        return "user", f"u:{claims.get('sub')}", name
    sess = (session or "").strip()[:40]
    ip = _client_ip(request)
    anon_id = f"a:{sess}" if sess else f"ip:{ip}"
    return "anon", anon_id, "匿名访客"


@app.post("/api/activity")
async def api_activity(request: Request) -> dict[str, Any]:
    """记录一条操作流水（登录账号精确到人，匿名访客按会话+IP）。公开端点、失败不报错。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    action = str((body or {}).get("action") or "").strip()
    if not _ACTIVITY_ACTION_RE.match(action):  # 格式合法即记（覆盖所有操作）；脏串拒掉
        return {"ok": False}
    target = str((body or {}).get("target") or "").strip()[:200]
    session = str((body or {}).get("session") or "").strip()
    kind, actor_id, name = _resolve_actor(request, session)
    ua = request.headers.get("user-agent") or ""
    metrics_log_activity(
        actor_kind=kind, actor_id=actor_id, actor_name=name, action=action,
        target=target, ip=_client_ip(request),
        device=("mobile" if _MOBILE_UA_RE.search(ua) else "pc"),
    )
    return {"ok": True}


@app.get("/api/metrics/activity")
async def api_metrics_activity(request: Request, token: str = "", actor: str = "", action: str = "", limit: int = 300) -> dict[str, Any]:
    """操作流水（管理员，需令牌）：按账号聚合 + 最近明细。?actor= 看某账号的全部操作。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    return {
        "stats": metrics_activity_stats(),
        "actors": metrics_activity_actors(80),
        "recent": metrics_recent_activity(limit=limit, action=action.strip(), actor_id=actor.strip()),
    }


@app.get("/api/headlines")
async def api_headlines() -> dict[str, Any]:
    """AI 评选的今日头条（快讯/文章/研报各最多 3 条、按重要性排序，附"为什么重要"）。"""
    return _HEADLINES


@app.get("/api/review/today")
async def api_review_today() -> dict[str, Any]:
    """最新一期 A股收盘复盘（首页置顶卡片用）。无则返回 {exists:false}。"""
    rv = ashare_review.latest_review()
    if not rv:
        return {"exists": False}
    return {"exists": True, "review": rv}


@app.get("/api/review/list")
async def api_review_list(limit: int = 60) -> dict[str, Any]:
    """历史复盘列表（轻量摘要，新→旧）。"""
    return {"items": ashare_review.list_reviews(limit=max(1, min(int(limit or 60), 120)))}


@app.get("/api/track-record")
async def api_track_record(request: Request) -> dict[str, Any]:
    """「我们提前发现的」量化战绩：只统计经 AI 判定层验证的命中(真实可溯源，不强行归因)。
    平台战绩公开(信任引流)；登录则附「你的自选相关」个人战绩(保守匹配)。"""
    hits = track_record._collect_hits(30)
    tr = {**track_record._summarize(hits), "days": 30, "recent": hits[:12]}
    claims = current_claims(request)
    if claims:
        wl = get_user_watchlist(str(claims.get("sub", "")))
        names = (list((wl.get("names") or {}).values()) + list(wl.get("symbols") or [])) if wl else []
        tr["personal"] = track_record.personal_track_record(hits, names)
    return tr


@app.get("/api/review/{date_str}")
async def api_review_by_date(date_str: str) -> dict[str, Any]:
    """某一天的完整复盘。"""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str or ""):
        raise HTTPException(status_code=422, detail="日期格式需为 YYYY-MM-DD")
    rv = ashare_review.review_for_date(date_str)
    if not rv:
        raise HTTPException(status_code=404, detail="该日期暂无复盘")
    return {"exists": True, "review": rv}


# ============================================================================ #
# 合作方 / 开发者 API（/api/v1/*）——只对外开放「自有内容」(复盘 / 速判卡)。
# 第三方版权内容(投行研报原文、聚合快讯/文章全文)绝不在此暴露。
# 访问凭 Header `X-API-Key`，按 key 限流 + 计量。
# ============================================================================ #
def _require_api_key(request: Request) -> dict:
    """校验合作方 API Key + 防暴破 + 限流 + 配额**检查**（不计量）。返回 key 记录。

    密钥仅从 Header `X-API-Key` 读取（不走 query，避免被 access log/代理记录泄露）。
    无效密钥按来源 IP 限速（防穷举/打认证层 DoS）。**计量延后到 handler 成功返回后**
    （见 _count_v1_success）——5xx/拒绝不耗配额、不计费，确保「按成功计」名实一致。"""
    from . import partner_api
    path = request.url.path
    ip = _client_ip(request)
    if partner_api.auth_fail_blocked(ip):
        raise HTTPException(status_code=429, detail="无效请求过多，请稍后再试")
    key = (request.headers.get("X-API-Key") or "").strip()
    rec = partner_api.verify_key(key)
    if not rec:
        partner_api.register_auth_fail(ip)
        raise HTTPException(status_code=401, detail="无效（含前缀非 dfk_/查无此 key）、已吊销或已过期的 API Key（请在 Header 传 X-API-Key）")
    kh, kp = rec["_key_hash"], rec.get("key_prefix", "")
    # 1) 频率（每分钟）——按请求计（成败都占速率窗）
    if not partner_api.check_rate(kh, int(rec.get("rate_per_min") or 60)):
        partner_api.log_usage(kp, path, 429, ip)
        raise HTTPException(status_code=429, detail=f"超出速率上限（{rec.get('rate_per_min')} 次/分钟），请稍后再试")
    # 2) 总次数配额（0=不限；按成功计——读 call_count）
    max_calls = int(rec.get("max_calls") or 0)
    if max_calls > 0 and int(rec.get("call_count") or 0) >= max_calls:
        partner_api.log_usage(kp, path, 403, ip)
        raise HTTPException(status_code=403, detail=f"已达该密钥的总调用次数上限（{max_calls} 次），请联系 DeepFocus 续期或升级")
    # 3) 每日配额（0=不限；按今日成功计——读非有损日计数表，按 key_hash）
    daily_quota = int(rec.get("daily_quota") or 0)
    if daily_quota > 0 and partner_api.today_count(kh) >= daily_quota:
        partner_api.log_usage(kp, path, 429, ip)
        raise HTTPException(status_code=429, detail=f"已达该密钥的每日调用上限（{daily_quota} 次/天），请明日再试或升级")
    return rec


def _count_v1_success(rec: dict, request: Request) -> None:
    """handler 成功产出后调用：记一次真实成功（日计数 +1 / call_count +1 / 明细）。
    放在 handler 末尾——若中途抛 5xx 则不会执行，故失败不耗配额、不计费。"""
    from . import partner_api
    partner_api.record_success(rec["_key_hash"], rec.get("key_prefix", ""), request.url.path, _client_ip(request))


_PARTNER_API_DOCS_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeepFocus Open API · 开发者文档</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--line:#222733;--text:#e6ebf2;--mute:#8a93a3;--amber:#ffb000;--green:#2bd96a;--blue:#6ab0ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 10px;padding-top:10px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 6px;color:var(--amber)}
.sub{color:var(--mute)}code{background:#0c1018;border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#9fd0ff}
pre{background:#0c1018;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:#cdd6e3}
.ep{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.m{display:inline-block;background:rgba(43,217,106,.15);color:var(--green);border:1px solid rgba(43,217,106,.4);border-radius:5px;padding:1px 8px;font-size:12px;font-weight:700;margin-right:8px}
.path{font-family:ui-monospace,Menlo,monospace;font-size:14px;color:#fff}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}th,td{border:1px solid var(--line);padding:6px 9px;text-align:left}th{color:var(--mute);font-weight:600}
.note{background:rgba(255,176,0,.08);border:1px solid rgba(255,176,0,.3);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13.5px}
a{color:var(--blue)}.ft{margin-top:40px;color:var(--mute);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
</style></head><body><div class="wrap">
<h1>DeepFocus Open API <span class="sub">v1</span></h1>
<p class="sub">DeepFocus 金融终端面向合作方/开发者的只读内容 API。基地址 <code>https://daocaijing.com</code>，所有业务端点形如 <code>/api/v1/*</code>，返回 JSON。</p>
<div class="note">⚠️ 仅开放 DeepFocus 自有内容（A股复盘、个股速判卡、资讯流、研报）。密钥由 DeepFocus 按需签发，无自助注册——请联系商务对接。</div>

<h2>鉴权</h2>
<p>每个业务请求都需在 HTTP Header 传入密钥：</p>
<pre>X-API-Key: dfk_xxxxxxxxxxxxxxxxxxxxxxxx</pre>
<ul>
<li>密钥<b>只从 Header 读取</b>，切勿放进 URL query（避免被访问日志/反向代理记录泄露）。</li>
<li>密钥仅在签发时完整返回<b>一次</b>，请妥善保存；它是服务端机密，<b>只在你自己的后端使用</b>，不要下发到浏览器/App 客户端。</li>
<li>公开索引 <code>GET /api/v1</code> 与本文档无需密钥。</li>
</ul>

<h2>端点</h2>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/review/today</span>
<p class="sub">最新一期 A股复盘（盘中=午盘版，收盘后=收盘版）。无复盘时 <code>exists:false</code>。</p>
<pre>curl -s https://daocaijing.com/api/v1/review/today -H 'X-API-Key: dfk_xxx'</pre>
<pre>{ "exists": true, "review": { "date": "2026-06-12", "session_label": "收盘复盘",
  "narrative": { "one_liner": "…一句话盘面…", "market": "…", "sectors": "…", "tomorrow": "…" },
  "our_edge": [ { "name": "某标的", "pct": 3.2, "lead_hours": 18 } ] } }</pre></div>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/review/{date}</span>
<p class="sub">指定日期（<code>YYYY-MM-DD</code>）的完整复盘。无则 404。</p>
<pre>curl -s https://daocaijing.com/api/v1/review/2026-06-12 -H 'X-API-Key: dfk_xxx'</pre></div>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/reviews</span>
<p class="sub">历史复盘列表（轻量摘要，新→旧）。</p>
<table><tr><th>参数</th><th>必填</th><th>说明</th></tr><tr><td>limit</td><td>否</td><td>条数，默认 30，上限 120</td></tr></table>
<pre>curl -s "https://daocaijing.com/api/v1/reviews?limit=10" -H 'X-API-Key: dfk_xxx'</pre></div>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/stock/{symbol}/verdict</span>
<p class="sub">个股证据速判卡（确定性引擎，多维证据 + 信号灯 + 可信度）。</p>
<table><tr><th>参数</th><th>必填</th><th>说明</th></tr>
<tr><td>symbol</td><td>是</td><td>标的代码，如 AAPL / 00700 / 600519（路径参数）</td></tr>
<tr><td>name / market</td><td>否</td><td>名称 / 市场，辅助消歧</td></tr></table>
<pre>curl -s https://daocaijing.com/api/v1/stock/AAPL/verdict -H 'X-API-Key: dfk_xxx'</pre></div>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/news</span>
<p class="sub">资讯流：快讯 / 文章。</p>
<table><tr><th>参数</th><th>必填</th><th>说明</th></tr>
<tr><td>topic</td><td>否</td><td><code>快讯</code> 或 <code>文章</code>，留空=全部</td></tr>
<tr><td>symbol</td><td>否</td><td>按标的过滤</td></tr><tr><td>q</td><td>否</td><td>关键词</td></tr>
<tr><td>limit</td><td>否</td><td>默认 80，上限 200</td></tr></table>
<pre>curl -s "https://daocaijing.com/api/v1/news?topic=%E5%BF%AB%E8%AE%AF&limit=20" -H 'X-API-Key: dfk_xxx'</pre>
<p class="sub">注：中文参数需 URL 编码（<code>快讯</code> → <code>%E5%BF%AB%E8%AE%AF</code>）。返回 <code>{ "count": N, "messages": [...] }</code>。</p></div>

<div class="ep"><span class="m">GET</span><span class="path">/api/v1/research</span>
<p class="sub">研报流（标题 / 机构 / 日期 / 原文预览链接）。</p>
<table><tr><th>参数</th><th>必填</th><th>说明</th></tr>
<tr><td>q</td><td>否</td><td>检索关键词，留空=最新</td></tr><tr><td>limit</td><td>否</td><td>默认 60，上限 200</td></tr></table>
<pre>curl -s "https://daocaijing.com/api/v1/research?limit=20" -H 'X-API-Key: dfk_xxx'</pre></div>

<h2>限流与配额</h2>
<p>每把密钥可设三道闸，超限不消耗已成功的配额：</p>
<table><tr><th>维度</th><th>说明</th><th>超限返回</th></tr>
<tr><td>速率 rate_per_min</td><td>每分钟请求数（按请求计，成败都占）</td><td><code>429</code></td></tr>
<tr><td>总次数 max_calls</td><td>密钥生命周期内成功调用上限（0=不限）</td><td><code>403</code></td></tr>
<tr><td>每日 daily_quota</td><td>每天（UTC）成功调用上限（0=不限）</td><td><code>429</code></td></tr></table>
<div class="note">配额<b>只按成功（HTTP 200）调用计</b>：被限流、鉴权失败或服务端错误（5xx）的请求不计入次数、不耗配额、不计费。每日配额按 UTC 自然日重置。</div>

<h2>错误码</h2>
<table><tr><th>状态码</th><th>含义</th></tr>
<tr><td>200</td><td>成功</td></tr>
<tr><td>401</td><td>密钥无效（含前缀非 dfk_/查无此 key）、已吊销、已过期，或未在 Header 传 X-API-Key</td></tr>
<tr><td>403</td><td>已达总次数上限（max_calls）</td></tr>
<tr><td>404</td><td>资源不存在（如该日期无复盘）</td></tr>
<tr><td>422</td><td>参数格式错误（如日期非 YYYY-MM-DD）</td></tr>
<tr><td>429</td><td>超出每分钟速率、每日配额，或无效请求过多（IP 限速）</td></tr>
<tr><td>502</td><td>上游数据源暂时不可用（速判卡生成失败 / 研报源不可用），稍后重试</td></tr></table>

<p class="ft">内容仅供研究参考，不构成投资建议。© DeepFocus 金融终端 · daocaijing.com</p>
</div></body></html>"""


@app.get("/api/v1")
async def api_v1_index() -> dict[str, Any]:
    """开发者 API 自描述索引（公开，无需 key）。说明可用端点与鉴权方式。"""
    return {
        "service": "DeepFocus Open API",
        "version": "v1",
        "docs": "https://daocaijing.com/api/v1/docs",
        "auth": "在请求 Header 传 X-API-Key: <你的密钥>（仅 Header，勿放 URL 参数）。密钥由 DeepFocus 签发，请妥善保管、仅服务端使用。",
        "note": "DeepFocus 金融终端内容 API。",
        "endpoints": [
            {"method": "GET", "path": "/api/v1/review/today", "desc": "最新一期 A股收盘复盘"},
            {"method": "GET", "path": "/api/v1/review/{date}", "desc": "指定日期(YYYY-MM-DD)的复盘"},
            {"method": "GET", "path": "/api/v1/reviews?limit=30", "desc": "历史复盘列表(摘要)"},
            {"method": "GET", "path": "/api/v1/stock/{symbol}/verdict", "desc": "个股证据速判卡(确定性引擎)"},
            {"method": "GET", "path": "/api/v1/news?topic=快讯&limit=80", "desc": "资讯流(快讯/文章，可按 symbol、q 过滤)"},
            {"method": "GET", "path": "/api/v1/research?q=&limit=60", "desc": "研报流(标题/机构/日期/预览链接)"},
        ],
        "disclaimer": "内容仅供研究参考，不构成投资建议。",
    }


@app.get("/api/v1/docs", response_class=HTMLResponse)
async def api_v1_docs() -> HTMLResponse:
    """开发者文档页（公开 HTML）：鉴权 / 端点 / 参数 / 示例 / 限流配额 / 错误码。"""
    return HTMLResponse(_PARTNER_API_DOCS_HTML)


@app.get("/api/v1/review/today")
async def api_v1_review_today(request: Request) -> dict[str, Any]:
    rec = _require_api_key(request)
    rv = ashare_review.latest_review()
    _count_v1_success(rec, request)
    return {"exists": bool(rv), "review": rv} if rv else {"exists": False}


@app.get("/api/v1/reviews")
async def api_v1_reviews(request: Request, limit: int = 30) -> dict[str, Any]:
    rec = _require_api_key(request)
    out = {"items": ashare_review.list_reviews(limit=max(1, min(int(limit or 30), 120)))}
    _count_v1_success(rec, request)
    return out


@app.get("/api/v1/review/{date_str}")
async def api_v1_review_by_date(request: Request, date_str: str) -> dict[str, Any]:
    rec = _require_api_key(request)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str or ""):
        raise HTTPException(status_code=422, detail="日期格式需为 YYYY-MM-DD")
    rv = ashare_review.review_for_date(date_str)
    if not rv:
        raise HTTPException(status_code=404, detail="该日期暂无复盘")
    _count_v1_success(rec, request)
    return {"exists": True, "review": rv}


@app.get("/api/v1/stock/{symbol}/verdict")
async def api_v1_stock_verdict(request: Request, symbol: str, name: str = "", market: str = "") -> dict[str, Any]:
    """个股证据速判卡（DeepFocus 确定性引擎自生成；不叠加 LLM 叙述以保证稳定）。"""
    rec = _require_api_key(request)
    sym = (symbol or "").strip().upper()[:16]
    if not sym:
        raise HTTPException(status_code=422, detail="缺少标的代码")
    try:
        # 对外合作方 API：硬编码 use_ifind=False —— iFinD 数据禁止裸转分发，绝不进 /api/v1。
        sheet = await _build_stock_tear_sheet_core(sym, name=name.strip(), market=market.strip(), use_ifind=False)
    except Exception:  # noqa: BLE001 —— 5xx 在此抛出 → 不会执行下面的计量 → 不耗配额/不计费
        raise HTTPException(status_code=502, detail="速判卡生成失败，请稍后再试")
    out = sheet.model_dump() if hasattr(sheet, "model_dump") else dict(sheet)
    _count_v1_success(rec, request)
    return out


@app.get("/api/v1/news")
async def api_v1_news(request: Request, topic: str = "", symbol: str = "", q: str = "", limit: int = 80) -> dict[str, Any]:
    """资讯流：快讯 / 文章（topic=快讯|文章，留空为全部）。可按 symbol 标的、q 关键词过滤。"""
    rec = _require_api_key(request)
    msgs = list_realtime_messages(
        topic=(topic.strip() or None),
        symbol=(symbol.strip() or None),
        q=(q.strip() or None),
        limit=max(1, min(int(limit or 80), 200)),
    )
    items = [m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in msgs]
    _count_v1_success(rec, request)
    return {"count": len(items), "messages": items}


@app.get("/api/v1/research")
async def api_v1_research(request: Request, q: str = "", limit: int = 60) -> dict[str, Any]:
    """海外投行研报流（标题 / 机构 / 日期 / 原文预览链接）。空 q=最新，带 q=检索。"""
    rec = _require_api_key(request)
    try:
        data = await fetch_research_wire_online(limit=max(1, min(int(limit or 60), 200)), query=q.strip())
    except Exception:  # noqa: BLE001 —— 研报源不可用 → 502 → 不计量、不耗配额
        raise HTTPException(status_code=502, detail="研报源暂时不可用，请稍后再试")
    items = data.get("items", []) if isinstance(data, dict) else []
    _count_v1_success(rec, request)
    return {"count": len(items), "items": items}


# --- 合作方 API Key 管理（管理员，需 metrics 令牌）---
def _check_metrics_token(request: Request, token: str) -> None:
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")


@app.post("/api/admin/partner-keys")
async def api_admin_partner_key_create(request: Request, token: str = "") -> dict[str, Any]:
    """签发一个合作方 API Key（明文只此一次返回）。"""
    _check_metrics_token(request, token)
    from . import partner_api
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    b = body or {}
    return partner_api.generate_key(
        str(b.get("name") or ""), str(b.get("tier") or "basic"), b.get("rate_per_min"),
        b.get("expires_in_days"), b.get("max_calls"), b.get("daily_quota"),
        price_cents=b.get("price_cents"), billing_period=str(b.get("billing_period") or ""),
        billing_status=str(b.get("billing_status") or ""), billing_note=str(b.get("billing_note") or ""),
        auto_renew=b.get("auto_renew"),
    )


@app.get("/api/admin/partner-keys")
async def api_admin_partner_keys(request: Request, token: str = "") -> dict[str, Any]:
    """合作方 key 列表 + 调用计量 + 计费汇总 + 续费/对账告警。"""
    _check_metrics_token(request, token)
    from . import partner_api
    return {"keys": partner_api.list_keys(), "usage": partner_api.usage_stats(),
            "billing": partner_api.billing_summary(), "alerts": partner_api.compute_alerts()}


@app.delete("/api/admin/partner-keys/{key}")
async def api_admin_partner_key_revoke(request: Request, key: str, token: str = "") -> dict[str, Any]:
    """吊销一个 key（软删，立即失效）。"""
    _check_metrics_token(request, token)
    from . import partner_api
    return {"revoked": partner_api.revoke_key(key)}


@app.post("/api/admin/partner-keys/{prefix}/billing")
async def api_admin_partner_key_billing(request: Request, prefix: str, token: str = "") -> dict[str, Any]:
    """更新某密钥计费信息：action=mark_paid（标记已收款）或直接传 billing 字段。"""
    _check_metrics_token(request, token)
    from . import partner_api
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    if str((b or {}).get("action") or "") == "mark_paid":
        ok = partner_api.mark_paid(prefix, str((b or {}).get("billing_note") or ""))
    else:
        fields = {k: v for k, v in (b or {}).items()
                  if k in ("price_cents", "billing_period", "billing_status", "billing_note", "auto_renew")}
        ok = partner_api.set_billing(prefix, **fields)
    return {"ok": ok}


@app.post("/api/admin/partner-keys/{prefix}/renew")
async def api_admin_partner_key_renew(request: Request, prefix: str, token: str = "") -> dict[str, Any]:
    """续期：在原到期日基础上延长（保留同一密钥，合作方无需改配置）。收款后操作。"""
    _check_metrics_token(request, token)
    from . import partner_api
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    days = int((b or {}).get("days") or 0)
    period = str((b or {}).get("billing_period") or "")
    new_exp = partner_api.extend_expiry(prefix, days=days, reset_period=period)
    if not new_exp:
        raise HTTPException(status_code=400, detail="续期失败：需传 days>0 或有效 billing_period，且密钥须存在")
    return {"ok": True, "expires_at": new_exp}


@app.post("/api/admin/review/generate")
async def api_review_generate(request: Request, token: str = "", force: bool = False) -> dict[str, Any]:
    """手动生成今日复盘（管理端，需令牌）。force=true 跳过交易日校验（便于测试）。"""
    if not _admin_token_ok(request, token):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    if not force and not await ashare_review.traded_today():
        return {"ok": False, "reason": "今日非交易日（上证最新日线≠今天）"}
    review = await ashare_review.build_review()
    if not ashare_review.save_review(review):
        return {"ok": False, "reason": "行情源暂不可用（东财限流/网络），仅取到指数、未取到板块/个股，未落库", "review": review}
    return {"ok": True, "review": review}


async def run_ashare_review() -> None:
    """A股复盘：每个交易日两场——11:40 午盘复盘（上午收盘后）、15:35 收盘复盘（全天定稿），自动生成并落库。
    交易日以「上证最新日线==今天」判定（免维护节假日表）；同日每场次独立去重（午盘不顶替收盘）。"""
    from .ashare_review import CN_TZ as _CNTZ
    await asyncio.sleep(30)
    print("[ashare-review] 启动：每交易日 11:40 午盘 / 15:35 收盘 生成 A股复盘")
    SLOTS = ((11, 40, "midday"), (15, 35, "close"))  # 北京时间
    while True:
        try:
            now = datetime.now(_CNTZ)
            # 选下一个最近的场次时间点
            cands = []
            for h, m, sess in SLOTS:
                t = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if t <= now:
                    t = t + timedelta(days=1)
                cands.append((t, sess))
            target, session = min(cands, key=lambda x: x[0])
            await asyncio.sleep(max(30, (target - now).total_seconds()))
            today = ashare_review.cn_today_str()
            if ashare_review.has_review_today(session):
                continue  # 今天这一场已生成
            if not await ashare_review.traded_today():
                continue  # 周末/节假日：上证日线不是今天
            review = await ashare_review.build_review(today)
            saved = ashare_review.save_review(review)
            tries = 0
            while not saved and tries < 4:  # 行情源不全（限流？）→ 当天最多重试 4×30min
                print(f"[ashare-review] {today} {session} 行情源不全，30 分钟后重试")
                await asyncio.sleep(1800)
                review = await ashare_review.build_review(today)
                saved = ashare_review.save_review(review)
                tries += 1
            if saved:
                print(f"[ashare-review] 已生成 {today} {ashare_review.session_label(session)}（提前发现 {len(review.get('our_edge') or [])} 条）")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[ashare-review] 异常：{type(exc).__name__}")
            await asyncio.sleep(300)


async def run_ai_fund_trader() -> None:
    """AI 模拟盘交易员：A股交易时段（北京时间周一~五 09:35–15:05）内每 30min 跑一轮多因子决策。

    启动 1 分钟后先补跑一轮（让面板随时有净值快照）；iFinD 未配置时 run_tick 自动空转、不臆造价格。"""
    await asyncio.sleep(60)
    roster = getattr(ai_fund, "ROSTER", None) or [None]   # 兼容：引擎未参数化时 [None] → 单账户
    print(f"[ai-fund] 启动：A股交易时段每 30min 跑 {len(roster)} 个智能体赛马（虚拟资金，不接券商）")

    async def _run_all(trade: bool) -> int:
        """对名单内每个智能体顺序跑一轮（数据缓存共享，首个预热后其余几乎零外网）。单个异常不拖垮全场。"""
        traded = 0
        for cfg in roster:
            try:
                out = await (asyncio.to_thread(ai_fund.run_tick, trade, cfg) if cfg is not None
                             else asyncio.to_thread(ai_fund.run_tick, trade))
                if out.get("ok"):
                    traded += len(out.get("traded") or [])
            except Exception as exc:  # noqa: BLE001
                fid = getattr(cfg, "fund_id", "main")
                print(f"[ai-fund] {fid} 异常：{type(exc).__name__}")
        return traded

    try:
        traded = await _run_all(True)  # 启动补跑（run_tick 内部按时段自动决定是否真交易）
        print(f"[ai-fund] 启动补跑：{len(roster)} 个智能体，本轮成交 {traded} 笔")
    except Exception as exc:  # noqa: BLE001
        print(f"[ai-fund] 启动补跑失败：{type(exc).__name__}")
    while True:
        try:
            now = datetime.now(ai_fund.BJ_TZ)
            in_session = (
                now.weekday() < 5
                and ((now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute == 0))
            )
            # 盘中 trade=True 激进交易；盘后/周末 trade=False 只刷研判+产出「观察」旁白+盯市，让面板 24h 有动静。
            traded = await _run_all(in_session)
            if traded:
                print(f"[ai-fund] {now:%H:%M} 全场成交 {traded} 笔")
            await asyncio.sleep(300 if in_session else 1200)  # 盘中 5min 活跃 / 盘后 20min 观察
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[ai-fund] 异常：{type(exc).__name__}")
            await asyncio.sleep(300)


def _aifund_snapshot(strategy: str) -> dict[str, Any]:
    """调 get_snapshot；兼容引擎是否已按策略参数化(多策略竞技场过渡期)：支持则传 strategy，否则回退主账户。"""
    try:
        return ai_fund.get_snapshot(strategy) if strategy else ai_fund.get_snapshot()
    except TypeError:
        return ai_fund.get_snapshot()  # 引擎尚未参数化 → 主账户


@app.get("/api/ai-fund/snapshot")
async def api_ai_fund_snapshot(request: Request, strategy: str = "") -> dict[str, Any]:
    """AI 模拟盘当前全貌（公开展示）：净值/收益、持仓盯市、近期交易理由、净值曲线、可信度。
    ?strategy=<fund_id> 取竞技场中某个选手的详情（缺省=主账户阿尔法，向后兼容）。

    展示型只读接口，免登录可看（与复盘/快讯等公开内容一致，降获客摩擦）。"""
    return await asyncio.to_thread(_aifund_snapshot, strategy)


@app.get("/api/ai-fund/arena")
async def api_ai_fund_arena(request: Request) -> dict[str, Any]:
    """AI 策略竞技场排行榜（公开展示）：阿尔法 vs 价值/趋势/事件/逆向各派 + 沪深300 基准，
    各选手累计收益/净值火花线/当前观点/排名。引擎 get_arena() 就绪后即生效；未就绪则降级提示（前端可回退主账户）。"""
    def _arena() -> dict[str, Any]:
        fn = getattr(ai_fund, "get_arena", None)
        if callable(fn):
            return fn()
        return {"ready": False, "strategies": [], "benchmark": None,
                "detail": "竞技场引擎正在接入中"}
    return await asyncio.to_thread(_arena)


@app.post("/api/ai-fund/tick")
async def api_ai_fund_tick(request: Request, payload: Optional[dict] = None) -> dict[str, Any]:
    """手动触发一轮模拟盘决策（管理员/运维，需 metrics 令牌）：用于种子/调试。"""
    _require_metrics_token(request, str((payload or {}).get("token") or ""))
    return await asyncio.to_thread(ai_fund.run_tick)


@app.get("/api/qr")
async def api_qr(data: str = "") -> dict[str, Any]:
    """把文本/网址编码成二维码模块矩阵（前端按矩阵在 Canvas 画 QR）。

    仅做编码、不抓取任何 URL，无 SSRF 风险；限长防滥用。"""
    text = (data or "").strip()[:512]
    if not text:
        raise HTTPException(status_code=422, detail="缺少 data")
    import qrcode  # 局部导入，避免无谓启动开销
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    matrix = [[1 if cell else 0 for cell in row] for row in q.get_matrix()]
    return {"size": len(matrix), "matrix": matrix}


@app.get("/api/metrics/review-quality")
async def api_metrics_review_quality(request: Request, token: str = "", limit: int = 60) -> dict[str, Any]:
    """复盘 AI 质量趋势（管理员，需令牌）：批评家初稿问题数 / 数字违规 / 修订率 / 残留违规，量化幻觉率随时间下降。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    return ashare_review.review_quality_stats(limit=max(1, min(int(limit or 60), 200)))


@app.get("/api/metrics/summary")
async def api_metrics_summary(request: Request, token: str = "") -> dict[str, Any]:
    """站点指标汇总（管理员）：累计/今日访问、研报下载总数、下载榜、近日趋势。

    需令牌：?token= 或请求头 X-Metrics-Token，匹配环境变量 DEEPFOCUS_METRICS_TOKEN。
    未配置令牌时拒绝访问（默认安全），避免指标公开。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    data = metrics_summary()
    try:
        data["accounts"] = account_stats()  # 账号体系数据并入看板汇总
    except Exception:  # 容错：账号统计失败不影响其余看板数据
        data["accounts"] = None
    try:
        data["invites"] = invite_stats()  # 拉新/邀请数据并入看板
    except Exception:
        data["invites"] = None
    return data


def _require_metrics_token(request: Request, token: str) -> None:
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")


@app.get("/api/metrics/growth")
async def api_metrics_growth(request: Request, token: str = "") -> dict[str, Any]:
    """增长分析（管理员，需 metrics 令牌）：实时 KPI + 最新 AI 分析报告 + 历史报告。"""
    _require_metrics_token(request, token)
    kpis = await asyncio.to_thread(growth_analytics.compute_kpis)
    return {
        "kpis": kpis,
        "latest": growth_analytics.latest_report(),
        "history": growth_analytics.report_history(limit=14),
    }


@app.post("/api/admin/growth-analyze")
async def api_admin_growth_analyze(request: Request, payload: Optional[dict] = None) -> dict[str, Any]:
    """手动触发一次增长分析（看板「重新分析」按钮，需 metrics 令牌）。"""
    _require_metrics_token(request, str((payload or {}).get("token") or ""))
    return await growth_analytics.generate_report(llm)


async def run_growth_analyst() -> None:
    """常驻增长分析师：每个自然日北京时间 16:20 生成 KPI + AI 改进建议报告。

    启动 3 分钟后若当日还没有报告，先补生成一份（保证看板随时有最新内容）。"""
    from .growth_analytics import BJ_TZ as _BJ
    await asyncio.sleep(180)
    print("[growth-analyst] 启动：每日 16:20 生成增长分析报告（用户/留存/日活/付费转化）")
    try:
        if not growth_analytics.has_report_today():
            out = await growth_analytics.generate_report(llm)
            print(f"[growth-analyst] 启动补生成 {out['day']} 报告（provider={out['provider']}）")
    except Exception as exc:  # noqa: BLE001
        print(f"[growth-analyst] 启动补生成失败：{exc}")
    while True:
        try:
            now = datetime.now(_BJ)
            target = now.replace(hour=16, minute=20, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(60, (target - now).total_seconds()))
            out = await growth_analytics.generate_report(llm)  # 每日定点全量重算（同日覆盖）
            print(f"[growth-analyst] 已生成 {out['day']} 报告（provider={out['provider']}）")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[growth-analyst] 本轮失败：{exc}，30 分钟后重试")
            await asyncio.sleep(1800)


async def run_t1_recall() -> None:
    """T+1 召回：每日北京时间 10:30（上午开盘时段，打开率最好）执行一轮。

    候选筛选 / 发信都在 t1_recall 模块里且绝不抛出；这里只管定时。"""
    from .t1_recall import run_t1_recall_once
    bj = timezone(timedelta(hours=8))
    await asyncio.sleep(240)
    print("[t1-recall] 启动：每日 10:30 给 24h 未回访的新注册用户发内容召回邮件")
    while True:
        try:
            now = datetime.now(bj)
            target = now.replace(hour=10, minute=30, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(60, (target - now).total_seconds()))
            out = await asyncio.to_thread(run_t1_recall_once)  # SMTP 是阻塞 IO，挪到线程
            print(f"[t1-recall] 本轮完成：候选{out['candidates']} 发送{out['sent']} 跳过{out['skipped']} 失败{out['errors']} 留待下轮{out.get('deferred', 0)}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[t1-recall] 本轮失败：{exc}，6 小时后重试")
            await asyncio.sleep(21600)


@app.post("/api/recall/t1/run")
async def api_t1_recall_run(request: Request, token: str = "") -> dict[str, Any]:
    """手动触发一轮 T+1 召回（管理员，需 metrics 令牌）：上线验证 / SMTP 配好后补发用。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    from .t1_recall import run_t1_recall_once
    return await asyncio.to_thread(run_t1_recall_once)


@app.get("/api/recall/t1/stats")
async def api_t1_recall_stats(request: Request, token: str = "") -> dict[str, Any]:
    """T+1 召回累计效果（管理员，需 metrics 令牌）。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    from .t1_recall import t1_recall_stats
    return t1_recall_stats()


async def run_expiry_reminder() -> None:
    """会员到期转化召回：每日北京时间 11:00 给 48h 内到期的免费会员发续费提醒。"""
    from .expiry_reminder import run_expiry_reminder_once
    bj = timezone(timedelta(hours=8))
    await asyncio.sleep(300)
    print("[expiry-reminder] 启动：每日 11:00 给 48h 内到期的免费会员发续费提醒")
    while True:
        try:
            now = datetime.now(bj)
            target = now.replace(hour=11, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(60, (target - now).total_seconds()))
            out = await asyncio.to_thread(run_expiry_reminder_once)
            print(f"[expiry-reminder] 本轮完成：候选{out['candidates']} 发送{out['sent']} 跳过{out['skipped']} 失败{out['errors']} 留待下轮{out.get('deferred', 0)}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[expiry-reminder] 本轮失败：{exc}，6 小时后重试")
            await asyncio.sleep(21600)


def _partner_alert_email_body(al: dict) -> str:
    """把 compute_alerts 结果拼成给管理员的中文汇总正文。无任何告警返回空串。"""
    cnt = al.get("counts", {})
    if not any(cnt.values()):
        return ""
    def _money(c):
        return f"¥{(c or 0) / 100:.0f}"
    lines = ["DeepFocus 合作方 API · 今日续费/对账提醒", ""]
    if al.get("near_expiry"):
        lines.append(f"⏰ 近到期（{len(al['near_expiry'])} 个，续费机会，收款后在看板点「续期」）：")
        for k in al["near_expiry"][:20]:
            lines.append(f"  · {k['name']}（{k['key_prefix']}…）{k['days_left']} 天后到期 · 约定价 {_money(k.get('price_cents'))}")
        lines.append("")
    if al.get("near_quota"):
        lines.append(f"⚠ 近配额（{len(al['near_quota'])} 个，建议升档销售线索）：")
        for k in al["near_quota"][:20]:
            lines.append(f"  · {k['name']}（{k['key_prefix']}…）{k['kind']}已用 {k['pct']}%")
        lines.append("")
    if al.get("unpaid_overdue"):
        lines.append(f"💰 待收款超期（{len(al['unpaid_overdue'])} 个，发钥超 7 天未到账，私信催款或吊销）：")
        for k in al["unpaid_overdue"][:20]:
            lines.append(f"  · {k['name']}（{k['key_prefix']}…）已 {k['days']} 天 · 约定价 {_money(k.get('price_cents'))}")
        lines.append("")
    if al.get("expired"):
        lines.append(f"⛔ 已过期断流（{len(al['expired'])} 个，待挽回）：")
        for k in al["expired"][:20]:
            lines.append(f"  · {k['name']}（{k['key_prefix']}…）{k['expires_at']} 到期")
        lines.append("")
    lines.append("到 daocaijing.com 运营看板「🔌 合作方 API」处理。")
    return "\n".join(lines)


async def run_partner_billing_alerts() -> None:
    """合作方 API 续费/对账告警：每日北京时间 09:30 汇总近配额/近到期/待收款发给管理员（有告警才发）。"""
    from . import partner_api
    from .recall_subscriptions import send_alert_email
    bj = timezone(timedelta(hours=8))
    await asyncio.sleep(360)
    print("[partner-alert] 启动：每日 09:30 汇总合作方 API 续费/对账告警给管理员")
    while True:
        try:
            now = datetime.now(bj)
            target = now.replace(hour=9, minute=30, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(60, (target - now).total_seconds()))
            al = await asyncio.to_thread(partner_api.compute_alerts)
            body = _partner_alert_email_body(al)
            if body:
                ok, msg = await asyncio.to_thread(send_alert_email, "[DeepFocus] 合作方 API 续费/对账提醒", body)
                print(f"[partner-alert] 告警 {al.get('counts')} 邮件：{ok} {msg}")
            else:
                print("[partner-alert] 今日无告警")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[partner-alert] 本轮失败：{exc}，6 小时后重试")
            await asyncio.sleep(21600)


@app.post("/api/recall/expiry/run")
async def api_expiry_reminder_run(request: Request, token: str = "") -> dict[str, Any]:
    """手动触发一轮到期转化召回（管理员，需 metrics 令牌）。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    from .expiry_reminder import run_expiry_reminder_once
    return await asyncio.to_thread(run_expiry_reminder_once)


@app.get("/api/recall/expiry/stats")
async def api_expiry_reminder_stats(request: Request, token: str = "") -> dict[str, Any]:
    """到期转化召回累计效果（管理员，需 metrics 令牌）。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    from .expiry_reminder import expiry_reminder_stats
    return expiry_reminder_stats()


@app.get("/api/metrics/members")
async def api_metrics_members(request: Request, token: str = "") -> dict[str, Any]:
    """会员运营数据（管理员，需 metrics 令牌）：会员名单 + 等级/到期/剩余天数 + 今日/7日/累计操作 + 续费预警。

    供 /api/metrics/dashboard 的「会员」区块。联系方式（手机号）完整返回——内部运营专用，
    靠 DEEPFOCUS_METRICS_TOKEN 保护、不对外。"""
    expected = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    provided = (token or request.headers.get("X-Metrics-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="需要有效的 metrics 令牌")
    try:
        users = list_users()
    except Exception:  # noqa: BLE001 —— 名单读取失败也返回空，不让看板整页崩
        users = []
    acts = metrics_member_activity()
    tier_label = {"lifetime": "永久", "premium": "尊享", "trial": "体验"}
    members: list[dict[str, Any]] = []
    summ = {"total": 0, "paid": 0, "lifetime": 0, "premium": 0, "trial": 0,
            "active_today": 0, "active_7d": 0, "expiring_7d": 0, "expiring_30d": 0, "ops_today": 0}
    for u in users:
        m = getattr(u, "membership", None) or {}
        tier = str(m.get("tier") or "trial")
        days_left = m.get("days_left")
        act = acts.get(f"u:{u.id}", {})
        today = int(act.get("today") or 0)
        week = int(act.get("week") or 0)
        total = int(act.get("total") or 0)
        summ["total"] += 1
        summ[tier] = summ.get(tier, 0) + 1
        if tier in ("premium", "lifetime"):
            summ["paid"] += 1
        if today > 0:
            summ["active_today"] += 1
        if week > 0:
            summ["active_7d"] += 1
        summ["ops_today"] += today
        if tier == "premium" and isinstance(days_left, int):
            if days_left <= 7:
                summ["expiring_7d"] += 1
            if days_left <= 30:
                summ["expiring_30d"] += 1
        members.append({
            "id": u.id,
            "name": u.username,
            "phone": u.phone or "",
            "role": u.role,
            "tier": tier,
            "tier_label": tier_label.get(tier, tier),
            "expires_at": m.get("expires_at"),
            "days_left": days_left,
            "ops_today": today,
            "ops_7d": week,
            "ops_total": total,
            "last_seen": act.get("last_seen") or "",
            "is_active": bool(getattr(u, "is_active", True)),
        })

    def _sort_key(x: dict[str, Any]):
        t = x["tier"]
        if t == "premium":
            dl = x["days_left"] if isinstance(x["days_left"], int) else 99999
            return (0, dl, -x["ops_today"])
        if t == "lifetime":
            return (1, 0, -x["ops_today"])
        return (2, 0, -x["ops_today"])

    members.sort(key=_sort_key)
    return {"summary": summ, "members": members, "generated_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/metrics/dashboard", response_class=HTMLResponse)
async def api_metrics_dashboard() -> HTMLResponse:
    """带令牌的可视化看板：在网址后加 ?token=... 直接打开看访问/研报/AI 数据。"""
    return HTMLResponse(_METRICS_DASHBOARD_HTML)


@app.get("/api/admin/metrics-token")
async def api_metrics_token(request: Request) -> dict[str, str]:
    """站长内置看板入口：登录用户若在 DEEPFOCUS_METRICS_OWNERS 名单(或管理员角色)，
    返回看板令牌 + 直达 URL。令牌不写进前端包，由站长登录态按需取，点一下即开看板。"""
    claims = require_current_user(request)
    # 运营看板永远只有 lx199710 可见：硬保证 lx199710 + 可经 env 显式追加；去掉「任意 admin」旁路。
    owners = {"lx199710"} | {u.strip().lower() for u in (os.getenv("DEEPFOCUS_METRICS_OWNERS") or "").split(",") if u.strip()}
    uname = str(claims.get("username") or "").strip().lower()
    if uname not in owners:
        raise HTTPException(status_code=403, detail="无权访问运营看板")
    tok = (os.getenv("DEEPFOCUS_METRICS_TOKEN") or "").strip()
    if not tok:
        raise HTTPException(status_code=500, detail="未配置看板令牌")
    return {"token": tok, "url": f"/api/metrics/dashboard?token={tok}"}


async def run_wire_refresher() -> None:
    """后台保活研报列表缓存：定期强制刷新默认视图（limit=120），让前端始终秒开。"""
    interval = float(os.getenv("DEEPFOCUS_WIRE_REFRESH_SECONDS", "75"))
    await asyncio.sleep(8)
    print(f"[wire-refresh] 启动：每 {interval}s 刷新研报列表缓存")
    while True:
        try:
            await fetch_research_wire_online(limit=120, use_cache=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[wire-refresh] 刷新失败：{type(exc).__name__}")
        await asyncio.sleep(interval)


async def run_cache_pruner() -> None:
    """每日清理过期 AI 解读缓存（默认 >90 天）。老研报/文章缓存极少再被读，删后即使再点也只重解析一次。
    DEEPFOCUS_AI_CACHE_MAX_AGE_DAYS=0 可关闭。"""
    days = int(os.getenv("DEEPFOCUS_AI_CACHE_MAX_AGE_DAYS", "90"))
    if days <= 0:
        print("[cache-prune] 未启用"); return
    await asyncio.sleep(180)  # 启动后稍等，避开冷启动繁忙期
    print(f"[cache-prune] 启动：每日清理 >{days} 天的 AI 解读缓存")
    while True:
        try:
            n = await asyncio.to_thread(metrics_prune_ai_cache, days)
            if n:
                print(f"[cache-prune] 已清理过期 AI 缓存 {n} 条")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[cache-prune] 异常：{type(exc).__name__}")
        await asyncio.sleep(86400)  # 每天一次


async def run_news_prewarm() -> None:
    """后台预解读最新「文章」类资讯并缓存（只挑内容够长的，快讯不解读以控成本）。"""
    if (os.getenv("DEEPFOCUS_NEWS_PREWARM", "1").strip().lower() in {"0", "false", "no"}):
        print("[news-prewarm] 未启用")
        return
    import hashlib as _hashlib
    per_cycle = int(os.getenv("DEEPFOCUS_NEWS_PREWARM_PER_CYCLE", "12"))
    interval = float(os.getenv("DEEPFOCUS_NEWS_PREWARM_CYCLE_SECONDS", "300"))
    await asyncio.sleep(40)
    print(f"[news-prewarm] 启动：每轮 {per_cycle} 篇文章、周期 {interval}s")
    while True:
        done = 0
        try:
            msgs = list_realtime_messages(topic="文章", limit=40)
            for m in msgs:
                if done >= per_cycle:
                    break
                title = (m.title or "").strip()
                content = (m.content or "").strip()
                if len(title + content) < 60:  # 太短不值得解读
                    continue
                key = "news:" + _hashlib.sha1(f"{title}\n{content}".encode("utf-8")).hexdigest()[:20]
                if metrics_get_ai_cache(key):
                    continue
                try:
                    result = await analyze_news(title, content)
                    metrics_set_ai_cache(key, result)
                    done += 1
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(2)
            if done:
                print(f"[news-prewarm] 本轮预解读 {done} 篇文章")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[news-prewarm] 异常：{type(exc).__name__}")
        await asyncio.sleep(interval)


_HEADLINES: dict[str, Any] = {"kx": [], "wz": [], "yb": [], "generated_at": ""}
# 头条粘性：记录每条头条「首次出现时刻」，保留窗口内不被换掉，避免每轮 LLM 重选导致「总是换」。
_HEADLINE_FIRST_SEEN: dict[str, float] = {}
_HEADLINE_HOLD_SECONDS = float(os.getenv("DEEPFOCUS_HEADLINE_HOLD_SECONDS", "1800"))  # 头条最短保留 30 分钟


def _hl_key(it: dict) -> str:
    return str((it or {}).get("id") or (it or {}).get("file_id") or (it or {}).get("title") or "").strip()


def _hl_similar(t1: str, t2: str) -> bool:
    """同事件改写稿判定：不同稿源把同一条新闻换词转写（称/说、袭击/打击、语序调整），
    精确 key 和编辑距离都抓不住 → 用字符二元组 Jaccard 相似度。数字序列不同(如不同百分比/数量)绝不判同。"""
    import re as _re
    a = _re.sub(r"[\s\W_]+", "", str(t1 or "")).lower()
    b = _re.sub(r"[\s\W_]+", "", str(t2 or "")).lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if "".join(c for c in a if c.isdigit()) != "".join(c for c in b if c.isdigit()):
        return False
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return False
    inter = len(ga & gb)
    return inter / max(1, len(ga | gb)) >= 0.41  # 0.41：实测卡在「同事件改写(0.417)合并、同实体不同事件(0.400)不合并」之间


def _stabilize_headlines(cat: str, fresh: list[dict], max_n: int = 3) -> list[dict]:
    """让头条有粘性：保留上一轮仍在窗口内(<HOLD)的旧头条，只把『本轮新选且尚未在榜』的重要头条加到最前、
    超出 max_n 时挤掉最旧的。→ 真正的新头条能上来，但 LLM 每轮重选不同旧故事不再造成抖动。"""
    now = time.time()
    fresh = [it for it in (fresh or []) if _hl_key(it)]
    # 只有「从没见过」的才算全新头条（避免被挤掉的旧头条又被重选回来 → 反复横跳）
    brand_new = {_hl_key(it) for it in fresh if _hl_key(it) not in _HEADLINE_FIRST_SEEN}
    for it in fresh:
        _HEADLINE_FIRST_SEEN.setdefault(_hl_key(it), now)
    # 仍在保留窗口内的旧头条（粘性保留）
    carried = [it for it in (_HEADLINES.get(cat) or [])
               if _hl_key(it) and (now - _HEADLINE_FIRST_SEEN.get(_hl_key(it), now) < _HEADLINE_HOLD_SECONDS)]
    carried_keys = {_hl_key(it) for it in carried}
    # 本轮『全新』且尚未在榜的头条 → 置顶
    fresh_new = [it for it in fresh if _hl_key(it) in brand_new and _hl_key(it) not in carried_keys]
    seen: set = set()
    out: list[dict] = []
    for it in fresh_new + carried:          # 新的在上，旧的(粘性)在下
        k = _hl_key(it)
        if k in seen:
            continue
        # 同事件改写稿（不同稿源换词转写同一新闻）只保留先到的一条
        if any(_hl_similar(str(it.get("title") or ""), str(o.get("title") or "")) for o in out):
            seen.add(k)
            continue
        seen.add(k)
        out.append(it)
    out = out[:max_n]                       # 超额时从底部(最旧)截掉
    keep = {_hl_key(it) for it in out}      # GC 过期登记
    for k in [k for k in _HEADLINE_FIRST_SEEN if k not in keep and now - _HEADLINE_FIRST_SEEN.get(k, now) > _HEADLINE_HOLD_SECONDS]:
        _HEADLINE_FIRST_SEEN.pop(k, None)
    return out


def _hl_pack_msg(m: Any, why: str) -> dict[str, Any]:
    return {"id": m.id, "title": m.title, "content": m.content or "", "url": m.url or "",
            "created_at": m.created_at, "severity": m.severity, "why": why}


# 研报头条「兜底」用的够格判定：只认 宏观/策略/大类资产/行业/政策 类或龙头权重股深度报告，
# 把「某中小盘个股 + 日期」这类标题挡在头条之外（避免伯特利这种上头条）。
_YB_MACRO_KW = (
    "宏观", "策略", "经济", "市场", "大盘", "大类", "配置", "行业", "板块", "周观点", "复盘",
    "政策", "展望", "周期", "估值", "流动性", "通胀", "美联储", "降息", "加息", "专题", "周度",
    "月度", "利率", "汇率", "商品", "主题", "景气", "拐点", "方向", "供给", "需求", "信用",
    "资产", "如何看", "如何评估", "怎么看", "怎么配", "再平衡",
)
_YB_LEADERS = ("贵州茅台", "茅台", "腾讯", "英伟达", "苹果", "台积电", "宁德时代", "比亚迪", "阿里", "美的")
_YB_DATE_TAIL = re.compile(r"[-\s]*\d{6,8}$")


def _yb_fallback_eligible(item: dict) -> bool:
    title = str(item.get("title") or "")
    if any(k in title for k in _YB_MACRO_KW) or any(k in title for k in _YB_LEADERS):
        return True
    core = _YB_DATE_TAIL.sub("", title).strip()
    return len(core) >= 9  # 描述性长标题（多为宏观/专题），短公司名标题不够格


async def run_headline_picker() -> None:
    """AI 头条评选：以机构交易台标准，从今日快讯/文章/研报里挑真正驱动市场的头条。

    每类最多 3 条、按重要性从高到低排序；不够格的宁可少选甚至不选（宁缺毋滥）。"""
    if (os.getenv("DEEPFOCUS_HEADLINE_PICKER", "1").strip().lower() in {"0", "false", "no"}):
        print("[headline] 未启用"); return
    interval = float(os.getenv("DEEPFOCUS_HEADLINE_CYCLE_SECONDS", "240"))
    await asyncio.sleep(30)
    print(f"[headline] 启动：每 {interval}s 评选一次")
    while True:
        try:
            msgs = list_realtime_messages(limit=120)
            kx = [m for m in msgs if (m.topic or "") == "快讯"][:40]
            wz = [m for m in msgs if (m.topic or "") == "文章"][:25]
            try:
                reps = (await fetch_research_wire_online(limit=30)).get("items", [])[:25]
            except Exception:
                reps = []
            if not (kx or wz or reps):
                await asyncio.sleep(interval); continue
            lines = ["【快讯】"] + [f"k{i}. {m.title}" for i, m in enumerate(kx)]
            lines += ["【文章】"] + [f"w{i}. {m.title}" for i, m in enumerate(wz)]
            lines += ["【研报】"] + [f"y{i}. {r.get('title', '')}" for i, r in enumerate(reps)]
            prompt = (
                "你是财经资讯编辑，为读者筛选今日值得关注的重要资讯。"
                "下面是今天的快讯/文章/研报标题（带编号）。请按新闻重要性严格评选，"
                "只保留真正重大、有信息增量的——\n"
                "① 货币政策与流动性：美联储/主要央行利率决议、点阵图、QT/QE、关键票委表态；\n"
                "② 一级宏观数据超/不及预期：CPI/PCE、非农、失业率、GDP、PMI、零售；\n"
                "③ 地缘与政策冲击：战争升级、制裁、关税、重大立法/监管、主权债务/评级；\n"
                "④ 系统重要性公司事件：龙头财报大幅超预期或暴雷、重大并购/分拆、IPO、违约、关键业绩指引或旗舰产品；\n"
                "⑤ 大宗与汇率剧烈异动：原油/黄金/美元指数/主要货币对的趋势性突破。\n"
                "严格排除：日常公告、重复或同质信息、公关软文、影响面有限的小盘股个别消息、缺乏增量的复述。\n"
                "特别针对【研报】头条：只选 宏观/策略/大类资产配置/行业景气与拐点/重大政策解读 类，"
                "或标的为各市场龙头权重股（如 贵州茅台/腾讯/英伟达/苹果/台积电 这种量级）的深度报告；"
                "普通中小盘个股的首次覆盖或常规跟踪研报（如某汽车零部件、某区域小公司）一律不选。\n"
                "评选规则：\n"
                "- 每类（快讯/文章/研报）按重要性从高到低，最多选 3 条；不够格的宁可少选甚至一条不选（宁缺毋滥，严禁凑数）；\n"
                "- 同一事件只保留信息量最大的一条，跨类去重；\n"
                "- 为每条写一句不超过 28 字的客观摘要（这条讲了什么、涉及哪些主体），只陈述事实、不做涨跌方向判断、不含投资建议。\n"
                "只输出严格 JSON：{\"kx\":[\"k3\",\"k1\"],\"wz\":[\"w0\"],\"yb\":[\"y2\"],"
                "\"why\":{\"k3\":\"...\",\"k1\":\"...\",\"w0\":\"...\",\"y2\":\"...\"}}，"
                "数组按重要性排序、每个最多 3 项；某类没有够格的就给空数组 []。\n\n" + "\n".join(lines)
            )
            data = await CloudResearchLLM().complete_json(prompt, max_tokens=900, timeout_seconds=45)
            why_map = (data or {}).get("why") or {}
            if not isinstance(why_map, dict):
                why_map = {}

            def pick_list(tag: str, arr: list, key: str) -> list:
                codes = (data or {}).get(key)
                if codes is None:
                    codes = []
                elif not isinstance(codes, list):
                    codes = [codes]
                out, seen = [], set()
                for raw in codes:
                    v = str(raw or "").strip()
                    if not v or v.lower() == "null" or not v.startswith(tag) or v in seen:
                        continue
                    try:
                        idx = int(v[len(tag):])
                    except ValueError:
                        continue
                    if 0 <= idx < len(arr):
                        seen.add(v)
                        out.append((arr[idx], str(why_map.get(v) or "")))
                    if len(out) >= 3:
                        break
                return out

            kx_sel = pick_list("k", kx, "kx")
            wz_sel = pick_list("w", wz, "wz")
            yb_sel = pick_list("y", reps, "yb")
            # 保底：某类 AI 一条都没选中时，回退到最新/最高优先一条，确保每个模块至少显示一个头条
            if not kx_sel and kx:
                kx_sel = [(next((m for m in kx if (m.severity or "") == "critical"), kx[0]), "")]
            if not wz_sel and wz:
                wz_sel = [(wz[0], "")]
            if not yb_sel:
                # 研报兜底：只在有「够格」(宏观/策略/行业/龙头)报告时才补，避免中小盘个股上头条
                cand = next((r for r in reps if _yb_fallback_eligible(r)), None)
                if cand:
                    yb_sel = [(cand, "")]
            # 经稳定器：保留窗口内旧头条 + 仅加真正的新头条，避免每轮全换（用户反馈「头条总是换」）
            _HEADLINES["kx"] = _stabilize_headlines("kx", [_hl_pack_msg(m, why) for m, why in kx_sel])
            _HEADLINES["wz"] = _stabilize_headlines("wz", [_hl_pack_msg(m, why) for m, why in wz_sel])
            _HEADLINES["yb"] = _stabilize_headlines("yb", [{"id": r.get("id"), "title": r.get("title"), "filename": r.get("filename"),
                                 "file_id": r.get("file_id"), "out": r.get("out", ""), "date": r.get("date", ""),
                                 "instruments": _instruments_for(r.get("file_id")), "why": why} for r, why in yb_sel])
            _HEADLINES["generated_at"] = datetime.now(timezone.utc).isoformat()
            print(f"[headline] 评选完成 kx={len(kx_sel)} wz={len(wz_sel)} yb={len(yb_sel)}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[headline] 异常：{type(exc).__name__}: {str(exc)[:80]}")
        # 还没有任何头条（冷启动/刚遇瞬时 422）时快速重试，别让首页空着等满 240s
        have_any = any(_HEADLINES.get(k) for k in ("kx", "wz", "yb"))
        await asyncio.sleep(interval if have_any else 25.0)


async def run_research_prewarm() -> None:
    """后台预解读：把最新研报逐篇 AI 解读并写入缓存，用户点开即秒回。

    顺序执行 + 间隔，避免压垮模型；每轮只处理若干篇未缓存的，新研报下一轮补上。
    DEEPFOCUS_RESEARCH_PREWARM=0 可关闭。"""
    if (os.getenv("DEEPFOCUS_RESEARCH_PREWARM", "1").strip().lower() in {"0", "false", "no"}):
        print("[prewarm] 研报预解读未启用")
        return
    per_cycle = int(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_PER_CYCLE", "30"))
    gap = float(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_GAP_SECONDS", "3"))
    cycle = float(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_CYCLE_SECONDS", "1800"))
    workers = max(1, int(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_CONCURRENCY", "2")))
    # 每日下载预算：ZSXQ 有「单自然日下载量」限额，超了会整天拒绝下载。务必远低于其阈值。
    daily_max = int(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_DAILY_MAX", "40"))
    # 为「新报告」预留的额度：回填(补历史)只能用 daily_max - fresh_reserve 以内，绝不挤占新报告。
    fresh_reserve = int(os.getenv("DEEPFOCUS_RESEARCH_FRESH_RESERVE", "15"))
    backfill_cap = int(os.getenv("DEEPFOCUS_RESEARCH_BACKFILL_PER_CYCLE", "8"))
    _DL_KEY = "research_pdf_dl"  # 当日 PDF 下载计数（按自然日滚动）
    await asyncio.sleep(25)  # 启动后稍等，让服务与工作台就绪
    print(f"[prewarm] 启动：并发 {workers}、每日下载上限 {daily_max}（为新报告预留 {fresh_reserve}）、周期 {cycle}s")
    done_counter = {"n": 0}
    banned = {"hit": False}
    failed_fids: set[str] = set()  # 下载成功但解读失败的，本进程内不再重复下载（避免浪费当日预算；重启后再试）

    async def _warm_one(item: dict, sem: asyncio.Semaphore) -> None:
        fid = str(item.get("file_id") or "").strip()
        async with sem:
            if banned["hit"] or metrics_get_daily(_DL_KEY) >= daily_max:
                return
            try:
                content, _ = await _fetch_research_online_pdf(fid, item.get("filename", ""))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 下载失败：可能瞬时(网络)或命中限额；不拉黑，留待重试
                msg = str(exc)
                if "下载量异常" in msg or "拒绝下载" in msg or "下个自然日" in msg:
                    banned["hit"] = True
                    print("[prewarm] 命中 ZSXQ 下载限额，今日停止下载（明日自动恢复）")
                else:
                    print(f"[prewarm] 下载失败 {fid}: {type(exc).__name__}: {msg[:50]}")
                return
            metrics_incr(_DL_KEY)  # 下载成功即计入当日预算（无论解读成败，配额都已消耗）
            try:
                result = await analyze_pdf_auto(content, title=item.get("title", "研报"), max_pages=4)
                metrics_set_ai_cache(fid, result)
                done_counter["n"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 解读失败：拉黑，避免反复下载烧预算
                failed_fids.add(fid)
                print(f"[prewarm] 解读失败(已拉黑本轮) {fid}: {type(exc).__name__}: {str(exc)[:50]}")
            await asyncio.sleep(gap)

    while True:
        done_counter["n"] = 0
        banned["hit"] = False
        try:
            used = metrics_get_daily(_DL_KEY)
            total_room = max(0, daily_max - used)
            if total_room <= 0:
                print(f"[prewarm] 今日下载预算已用尽（{used}/{daily_max}），本轮跳过")
                await asyncio.sleep(cycle)
                continue
            data = await fetch_research_wire_online(limit=200)  # 整库覆盖（知识星球返回上限）
            fresh: list[dict] = []
            migrate: list[dict] = []
            for it in (data.get("items") or []):
                fid = str(it.get("file_id") or "").strip()
                if not fid or fid in failed_fids:
                    continue
                cached = metrics_get_ai_cache(fid)
                if not cached:
                    fresh.append(it)  # 新报告(无缓存)
                elif isinstance(cached, dict) and "instruments" not in cached:
                    migrate.append(it)  # 旧缓存补「提及标的」（市场归类由 _market_for 用 subject 即时算，无需重下载）
            # 新报告绝对优先（用满当日预算）；回填只用「预留新报告额度后」的剩余空间
            fresh_pending = fresh[:total_room]
            backfill_room = max(0, min(backfill_cap, (daily_max - fresh_reserve) - used, total_room - len(fresh_pending)))
            pending = fresh_pending + migrate[:backfill_room]
            if pending:
                print(f"[prewarm] 本轮处理 {len(pending)} 篇（新{len(fresh_pending)}/回填{len(pending) - len(fresh_pending)}），当日已下 {used}/{daily_max}")
                sem = asyncio.Semaphore(workers)
                await asyncio.gather(*[_warm_one(it, sem) for it in pending], return_exceptions=True)
                print(f"[prewarm] 本轮完成 {done_counter['n']} 篇，当日累计 {metrics_get_daily(_DL_KEY)}/{daily_max}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[prewarm] 本轮异常：{type(exc).__name__}: {str(exc)[:80]}")
        await asyncio.sleep(cycle)


@app.get("/api/research/search", response_model=ResearchReportSearchResponse)
async def api_research_search(
    keyword: str,
    market: Optional[str] = None,
    page_size: int = 20,
) -> ResearchReportSearchResponse:
    """研报融合检索（东方财富直连侧）：关键词→标的→A股/港股研报列表，附直链 PDF。

    美股无东财研报，返回空 + 警告，前端据此切到海外投行（知识星球）源。"""
    kw = (keyword or "").strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not kw:
        return ResearchReportSearchResponse(
            keyword=keyword, items=[], provider="none", fetched_at=fetched_at,
            warnings=["请输入公司、代码或主题"],
            data_quality=DataQuality(level="degraded", label="未检索", detail="缺少关键词"),
        )

    resolved = await search_market_symbols(kw, market=market)
    candidate = resolved.candidates[0] if resolved.candidates else None
    if not candidate:
        # 东财仅覆盖 A股/港股；美股及海外标的（如特斯拉）由海外投行源承接。
        return ResearchReportSearchResponse(
            keyword=keyword, items=[], provider="none", fetched_at=fetched_at,
            warnings=["东方财富(A股/港股)未匹配到该标的；海外/美股研报见下方「海外投行报告」结果"],
            data_quality=DataQuality(
                level="degraded", label="东财未命中",
                detail="未在 A股/港股 匹配到该标的，可改用海外投行源或更精确的代码",
                reasons=["eastmoney-no-match"],
            ),
        )

    resolved_market = candidate.market
    if resolved_market == "US":
        return ResearchReportSearchResponse(
            keyword=keyword, resolved_symbol=candidate.symbol, resolved_market=resolved_market,
            items=[], provider="eastmoney", fetched_at=fetched_at,
            warnings=[f"东方财富无{candidate.name}（美股）研报，请在海外投行报告源检索"],
            data_quality=DataQuality(
                level="degraded", label="东财无美股研报",
                detail="美股研报请使用海外投行（知识星球）源", reasons=["eastmoney-no-us"],
            ),
        )

    rows, query_warnings = await query_eastmoney_reports(
        code=candidate.code, market=resolved_market, page_size=page_size,
    )
    items = [
        ResearchReportItem(
            id=row["info_code"],
            title=row["title"],
            org=row["org"],
            date=row["date"],
            symbol=candidate.symbol,
            market=resolved_market,
            rating=row.get("rating") or None,
            stock_name=row.get("stock_name") or candidate.name,
            pdf_url=row["pdf_url"],
            preview_url=f"/api/research/pdf/{row['info_code']}",
        )
        for row in rows
    ]

    if items:
        data_quality = DataQuality(
            level="live", label="东方财富研报直连", detail=f"{candidate.name} · {len(items)} 篇",
        )
    else:
        data_quality = DataQuality(
            level="degraded", label="暂无研报", detail="该标的暂无东财研报", reasons=query_warnings,
        )

    return ResearchReportSearchResponse(
        keyword=keyword,
        resolved_symbol=candidate.symbol,
        resolved_market=resolved_market,
        items=items,
        provider="eastmoney" if items else "none",
        fetched_at=fetched_at,
        warnings=query_warnings,
        data_quality=data_quality,
    )


@app.get("/api/research/pdf/{info_code}")
async def api_research_pdf(info_code: str) -> StreamingResponse:
    """研报 PDF 在线预览代理（SSRF 安全：host 硬编码，仅放行 [A-Za-z0-9] 编号）。"""
    code = (info_code or "").strip()
    if not _RESEARCH_INFO_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail="非法的研报编号")

    pdf_url = eastmoney_report_pdf_url(code)
    client = httpx.AsyncClient(trust_env=False, timeout=_RESEARCH_PDF_TIMEOUT, follow_redirects=True)
    upstream_request = client.build_request(
        "GET", pdf_url,
        headers={"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"研报 PDF 拉取失败：{exc}") from exc

    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=404 if status == 404 else 502, detail=f"研报 PDF 不可用（HTTP {status}）")

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{code}.pdf"',
            "Cache-Control": "public, max-age=3600",
        },
    )


async def _resolve_research_pdf_bytes(request: ResearchVisionAnalyzeRequest) -> bytes:
    """取研报 PDF 字节：在线 file_id（经工作台）→ 本地抓取舱文件 → 东财 PDF 直链。"""
    if request.file_id:
        try:
            content, _ = await _fetch_research_online_pdf(
                request.file_id, request.filename or request.title or "",
            )
            return content
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"在线研报拉取失败：{str(exc)[:80]}") from exc

    if request.workbench_filename:
        path = _safe_workbench_file_path(request.workbench_out, request.workbench_filename)
        return path.read_bytes()

    url = (request.pdf_url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少研报来源（file_id / workbench_filename / pdf_url）")
    host = (urlparse(url).hostname or "").lower()
    if host != "pdf.dfcfw.com":
        raise HTTPException(status_code=400, detail="仅支持东方财富研报 PDF 直链或抓取舱文件的视觉解读")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_RESEARCH_PDF_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"研报 PDF 拉取失败：{exc}") from exc


# AI 解读并发闸：2 核/1.8G 小机器上，限制同时进行的 PDF 渲染+LLM 解读数，避免 CPU 打满/内存爆。
# 超出的请求排队等待（前端有 150s 超时兜底）；结果有缓存，重复点开秒回不占额度。
_AI_ANALYZE_SEM = asyncio.Semaphore(int(os.getenv("DEEPFOCUS_AI_ANALYZE_CONCURRENCY", "3")))


def _check_ai_quota(user: Optional[dict], kind: str, request: Optional[Request] = None, *, cached: bool = False) -> Optional[str]:
    """AI 解读额度闸（付费墙 + 省 token）。返回计数 key（成功后 incr）或 None（会员无限）。

    策略：
    - 会员(premium/lifetime)：无限 → None（生成 + 读取都放行）。
    - 非会员 / 未登录：**绝不触发新生成（省 token）**，只有结果【已缓存】才放行，且每天免费 1 次（研报/文章共享，env DEEPFOCUS_FREE_AI_READ）。
        · 未缓存 → 匿名抛 403（含「登录」→ 前端走登录弹窗）/ 登录非会员抛 402（→ 前端走升级弹窗）。
        · 已缓存但今日免费额度用完 → 同上提示。
    用 403（匿名）而非 401，避免前端拦截器把它当「登录失效」整页跳转。kind: yb(研报)/wz(文章)。"""
    # 会员 → 无限（生成/读取都放行）
    if user:
        uid = str(user.get("sub", ""))
        u = get_user_out_by_id(uid)
        tier = (u.membership or {}).get("tier") if (u and u.membership) else None
        if tier in ("premium", "lifetime"):
            return None
    free_limit = int(os.getenv("DEEPFOCUS_FREE_AI_READ", "1") or 1)
    label = "研报" if kind == "yb" else "文章"
    if not user:
        # 匿名：未缓存不生成、直接引导登录；已缓存每天免费读 free_limit 次
        ip = _client_ip(request) if request is not None else "?"
        fkey = f"q:aifree:anon:{ip}"
        if not cached:
            raise HTTPException(status_code=403, detail=f"该{label} AI 解读尚未生成——登录即可解读，还送 3 天尊享会员 🎁")
        if metrics_get_daily(fkey) >= free_limit:
            raise HTTPException(status_code=403, detail="今日免费体验已用完——登录即可继续解读，还送 3 天尊享会员 🎁")
        return fkey
    # 登录非会员：未缓存不生成、引导开通；已缓存每天免费读 free_limit 次
    uid = str(user.get("sub", ""))
    fkey = f"q:aifree:{uid}"
    if not cached:
        raise HTTPException(status_code=402, detail=f"AI 解读是会员功能——开通会员即可无限解读{label}（点右上角头像 💬 联系管理员开通，或 🎁 邀请好友得会员）。")
    if metrics_get_daily(fkey) >= free_limit:
        raise HTTPException(status_code=402, detail=f"今日免费 AI 解读已用完（非会员每天 {free_limit} 次）。开通会员畅享无限——点右上角头像 💬 联系管理员开通，或 🎁 邀请好友得会员。")
    return fkey


@app.post("/api/research/vision-analyze", response_model=ResearchVisionAnalysisResponse)
async def api_research_vision_analyze(
    request: ResearchVisionAnalyzeRequest,
    http_req: Request,
    _user: Optional[dict] = Depends(optional_current_user),  # 匿名也可：免费体验一次，之后引导登录
) -> ResearchVisionAnalysisResponse:
    """研报 AI 解读：文本优先 + 图片型回退视觉；按 file_id/文件名长期缓存，命中秒回。

    付费墙：非会员不触发新生成（省 token），仅命中缓存才放行、每天免费 1 次；会员无限。"""
    title = (request.title or "研报").strip()
    cache_key = (request.file_id or request.workbench_filename or request.pdf_url or "").strip()
    cached_result = metrics_get_ai_cache(cache_key) if cache_key else None  # 先探缓存，决定非会员能否放行
    quota_key = _check_ai_quota(_user, "yb", http_req, cached=cached_result is not None)
    metrics_incr("ai_research")  # 统计 AI 解读点击次数
    metrics_incr_ai_ref((request.file_id or request.workbench_filename or "").strip(), (request.title or "").strip())  # AI 解读榜

    def _build_response(result: dict[str, Any]) -> ResearchVisionAnalysisResponse:
        return ResearchVisionAnalysisResponse(
            title=title,
            symbol=request.symbol,
            subject=result.get("subject", ""),
            one_liner=result.get("one_liner", ""),
            summary=result["summary"],
            core_logic=result.get("core_logic", ""),
            takeaway=result.get("takeaway", ""),
            bullish=result.get("bullish", result.get("key_points", [])),
            bearish=result.get("bearish", result.get("risks", [])),
            key_points=result.get("key_points", []),
            risks=result.get("risks", []),
            rating=result.get("rating"),
            target_price=result.get("target_price"),
            confidence=result.get("confidence", 0.5),
            pages_analyzed=result.get("pages_analyzed", 0),
            provider=_AI_BRAND,  # 对外只露品牌，不暴露底层模型
            disclaimer=result.get("disclaimer", ""),
            data_quality=DataQuality(
                level="degraded", label="AI 解读",
                detail="AI 自动解读，非逐句溯源，请以原文为准", reasons=["ai-no-citation"],
            ),
        )

    if cached_result is not None:
        if quota_key: metrics_incr(quota_key)  # 命中缓存也计 1 次（非会员每日免费额度）
        return _build_response(cached_result)

    # 走到这里必为会员（非会员未缓存已被 _check_ai_quota 拦下，不会触发生成）
    pdf_bytes = await _resolve_research_pdf_bytes(request)
    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="未能获取研报 PDF 内容")
    try:
        async with _AI_ANALYZE_SEM:  # 并发闸：避免小机器同时跑太多解读
            result = await analyze_pdf_auto(
                pdf_bytes, title=title, symbol=request.symbol, max_pages=request.max_pages,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成 502，前端给友好提示
        raise HTTPException(status_code=502, detail=f"AI 解读失败：{exc}") from exc

    if cache_key:
        metrics_set_ai_cache(cache_key, result)
    if quota_key: metrics_incr(quota_key)
    return _build_response(result)


@app.post("/api/news/ai-analyze", response_model=ResearchVisionAnalysisResponse)
async def api_news_ai_analyze(
    request: NewsAnalyzeRequest,
    http_req: Request,
    _user: Optional[dict] = Depends(optional_current_user),  # 匿名也可：免费体验一次，之后引导登录
) -> ResearchVisionAnalysisResponse:
    """对一条推送文章/快讯做大白话 AI 解读（结构与研报一致，前端复用同一卡片）。按内容缓存。

    付费墙：非会员不触发新生成（省 token），仅命中缓存才放行、每天免费 1 次；会员无限。"""
    import hashlib as _hashlib
    title = (request.title or "").strip()
    content = (request.content or "").strip()
    cache_key = "news:" + _hashlib.sha1(f"{title}\n{content}".encode("utf-8")).hexdigest()[:20]
    cached_result = metrics_get_ai_cache(cache_key)  # 先探缓存，决定非会员能否放行
    quota_key = _check_ai_quota(_user, "wz", http_req, cached=cached_result is not None)
    metrics_incr("ai_news")  # 统计文章 AI 解读点击次数
    if title:  # 文章热度榜
        metrics_incr_news_heat("wz:" + _hashlib.sha1(title.encode("utf-8")).hexdigest()[:16], title)

    def _resp(result: dict[str, Any]) -> ResearchVisionAnalysisResponse:
        return ResearchVisionAnalysisResponse(
            title=title or "新闻解读", subject=result.get("subject", ""),
            one_liner=result.get("one_liner", ""), summary=result["summary"],
            core_logic=result.get("core_logic", ""), takeaway=result.get("takeaway", ""),
            bullish=result.get("bullish", []), bearish=result.get("bearish", []),
            key_points=result.get("key_points", []), risks=result.get("risks", []),
            rating=result.get("rating"), target_price=result.get("target_price"),
            confidence=result.get("confidence", 0.5), provider=_AI_BRAND,
            disclaimer=result.get("disclaimer", ""),
            data_quality=DataQuality(
                level="degraded", label="AI 解读",
                detail="AI 自动解读，仅供参考、非投资建议", reasons=["ai-no-citation"],
            ),
        )

    if cached_result is not None:
        if quota_key: metrics_incr(quota_key)
        return _resp(cached_result)
    # 走到这里必为会员（非会员未缓存已被 _check_ai_quota 拦下，不会触发生成）
    try:
        async with _AI_ANALYZE_SEM:  # 并发闸：与研报解读共用，避免小机器过载
            result = await analyze_news(title, content)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 解读失败：{exc}") from exc
    metrics_set_ai_cache(cache_key, result)
    if quota_key: metrics_incr(quota_key)
    return _resp(result)


@app.post("/api/data-sources/agent-crawl", response_model=DataSourceSyncResponse)
async def api_agent_crawl(request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await capture_agent_web_pages(request)
    publish_data_source_items(items, topic="data-source-crawl")
    return DataSourceSyncResponse(source=source, imported_count=len(items), items=items)


@app.post("/api/data-sources/keyword-crawl", response_model=DataSourceKeywordCrawlResponse)
async def api_keyword_crawl(request: DataSourceKeywordCrawlRequest) -> DataSourceKeywordCrawlResponse:
    source, items, warnings, meta = await keyword_crawl_data_source(request)
    publish_data_source_items(items, topic="data-source-keyword")
    return DataSourceKeywordCrawlResponse(
        provider=request.provider,
        effective_provider=meta["effective_provider"],
        attempted_providers=meta["attempted_providers"],
        fallback_used=meta["fallback_used"],
        provider_policy=meta["provider_policy"],
        keyword=request.keyword,
        sort=request.sort,
        freshness=request.freshness,
        source=source,
        imported_count=len(items),
        items=items,
        warnings=warnings,
    )


@app.get("/api/realtime/messages", response_model=RealtimeMessageListResponse)
async def api_list_realtime_messages(
    symbol: Optional[str] = None,
    topic: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
    q: Optional[str] = None,
    anyq: Optional[str] = None,
    limit: int = 80,
) -> RealtimeMessageListResponse:
    return RealtimeMessageListResponse(
        messages=list_realtime_messages(
            symbol=symbol,
            topic=topic,
            severity=severity,
            since=since,
            before=before,
            q=q,
            anyq=anyq,
            limit=max(1, min(limit, 200)),
        )
    )


@app.post("/api/realtime/messages", response_model=RealtimeMessageRecord)
async def api_push_realtime_message(request: RealtimeMessageCreateRequest) -> RealtimeMessageRecord:
    msg = create_realtime_message(request)
    if msg is None:  # 命中内容过滤(斧头/futou 等)→ 拒绝入库
        raise HTTPException(status_code=422, detail="内容被资讯过滤规则拦截")
    return msg


@app.get("/api/realtime/messages/stream")
async def api_realtime_message_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        realtime_message_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/realtime/recall/subscriptions", response_model=RecallSubscriptionRecord)
async def api_create_recall_subscription(request: RecallSubscriptionCreateRequest) -> RecallSubscriptionRecord:
    return create_recall_subscription(request)


@app.get("/api/realtime/recall/subscriptions", response_model=RecallSubscriptionListResponse)
async def api_list_recall_subscriptions() -> RecallSubscriptionListResponse:
    return RecallSubscriptionListResponse(subscriptions=list_recall_subscriptions(active_only=False))


@app.delete("/api/realtime/recall/subscriptions/{subscription_id}")
async def api_delete_recall_subscription(subscription_id: str) -> dict[str, bool]:
    return {"deleted": delete_recall_subscription(subscription_id)}


@app.get("/api/realtime/recall/webpush-key")
async def api_recall_webpush_key() -> dict[str, Any]:
    """前端订阅 Web Push 需要的 VAPID 公钥（公开、非敏感）。未配置→enabled=false，前端隐藏离线推送 UI。
    服务端须同时配 DEEPFOCUS_VAPID_PUBLIC_KEY（此处暴露）+ DEEPFOCUS_VAPID_PRIVATE_KEY/SUBJECT（推送时用）。"""
    pub = os.getenv("DEEPFOCUS_VAPID_PUBLIC_KEY", "").strip()
    return {"enabled": bool(pub), "public_key": pub}


# ===== 微信 iLink 渠道：扫码绑定到当前登录账号（多租户「扫码即问」）=====
_WEIXIN_MGR = None  # WeixinChannelManager；DEEPFOCUS_WEIXIN_CHANNEL=1 时由 lifespan 启动


def _require_weixin_user(request: Request) -> dict:
    """微信「扫码即问」——灰度内测，仅白名单账号（默认 lx199710，复用 iFinD 白名单）。否则 403。"""
    from . import ifind_api
    claims = require_current_user(request)
    if str(claims.get("username") or "").strip().lower() not in ifind_api.allowed_usernames():
        raise HTTPException(status_code=403, detail="微信扫码即问内测中，暂未对你的账号开放")
    return claims


def _require_premium_user(request: Request) -> dict:
    """微信扫码绑定/收快讯——尊享会员专享（premium/lifetime）。否则 403。"""
    from .auth import membership_of_username
    claims = require_current_user(request)
    m = membership_of_username(str(claims.get("username") or "")) or {}
    if m.get("tier") not in ("premium", "lifetime"):
        raise HTTPException(status_code=403, detail="微信扫码绑定为尊享会员专享，请先开通会员")
    return claims


@app.post("/api/weixin/bind/start")
async def api_weixin_bind_start(_user: dict = Depends(_require_premium_user)) -> dict:
    """登录用户发起绑定：取一张 iLink 二维码；前端按 qr_content 渲染成码让用户微信扫。"""
    from . import weixin_ilink
    try:
        data = await weixin_ilink.get_bot_qrcode()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取微信二维码失败：{str(exc)[:120]}")
    if data.get("ret") != 0 or not data.get("qrcode"):
        raise HTTPException(status_code=502, detail="iLink 未返回有效二维码")
    qr_content = data.get("qrcode_img_content") or data["qrcode"]
    return {
        "qrcode": data["qrcode"],
        "qr_content": qr_content,
        "qr_data_url": weixin_ilink.qr_png_data_url(qr_content),  # 后端渲染好的 PNG，前端 <img> 直接显示
        "base_url": weixin_ilink.ILINK_BASE,
    }


@app.get("/api/weixin/bind/status")
async def api_weixin_bind_status(qrcode: str, base_url: str = "", _user: dict = Depends(_require_premium_user)) -> dict:
    """前端轮询扫码状态；confirmed 时把绑定落到当前登录账号并即时起轮询。"""
    from . import weixin_bind, weixin_ilink
    try:
        status = await weixin_ilink.get_qrcode_status(qrcode, base_url or None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"查询扫码状态失败：{str(exc)[:120]}")
    st = status.get("status")
    if st == "scaned_but_redirect":
        host = (status.get("redirect_host") or "").replace("https://", "").replace("http://", "").strip("/")
        return {"status": st, "base_url": f"https://{host}" if host else (base_url or weixin_ilink.ILINK_BASE)}
    if st == "confirmed" and status.get("ilink_bot_id"):
        weixin_bind.upsert_binding(
            deepfocus_user_id=str(_user.get("sub") or ""),
            ilink_bot_id=status["ilink_bot_id"],
            token=status.get("bot_token") or "",
            base_url=status.get("baseurl") or weixin_ilink.ILINK_BASE,
            wechat_user_id=status.get("ilink_user_id"),
            username=str(_user.get("username") or ""),
        )
        if _WEIXIN_MGR is not None:
            _WEIXIN_MGR.add_account(status["ilink_bot_id"])
        return {"status": "confirmed", "bound": True}
    return {"status": st or "wait"}


@app.get("/api/weixin/bind/me")
async def api_weixin_bind_me(_user: dict = Depends(_require_premium_user)) -> dict:
    """当前用户的微信绑定状态（给「绑定微信」入口展示）。"""
    from . import weixin_bind
    b = weixin_bind.get_by_user(str(_user.get("sub") or ""))
    if not b:
        return {"bound": False, "channel_live": _WEIXIN_MGR is not None}
    wx = b.get("wechat_user_id") or ""
    return {
        "bound": bool(b.get("active")),
        "wechat_masked": (wx[:6] + "…" + wx[-6:]) if len(wx) > 14 else wx,
        "bound_at": b.get("bound_at"),
        "channel_live": _WEIXIN_MGR is not None,
        "push_scope": b.get("push_scope") or "off",       # 自助配置回显
        "push_symbols": b.get("push_symbols") or [],
    }


@app.post("/api/weixin/my-push-config")
async def api_weixin_my_push_config(request: Request, _user: dict = Depends(_require_premium_user)) -> dict:
    """会员自助设置自己的推送订阅(JWT)：{scope(off/watchlist/all), symbols[]}。只能改自己的绑定。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    from . import weixin_bind
    uid = str(_user.get("sub") or "")
    if not weixin_bind.get_by_user(uid):
        raise HTTPException(status_code=400, detail="你还没有绑定微信")
    rec = weixin_bind.set_push_config(uid, str(body.get("scope") or "off"), list(body.get("symbols") or []))
    return {"ok": True, "scope": (rec or {}).get("push_scope"), "symbols": (rec or {}).get("push_symbols")}


async def _weixin_noop_agent(_q: str, _h: str):
    return None


@app.post("/api/weixin/push")
async def api_weixin_push(text: str = "", fresh_minutes: int = 0, _admin: dict = Depends(require_admin)) -> dict:
    """管理端「准推送」：对已绑定且 context_token 仍有效的活跃用户 best-effort 发一条文本。
    ⚠️ 低频用（如每日一条摘要）；高频群发触微信反垃圾红线、有封号风险。
    fresh_minutes>0 时只推该时长内活跃过的用户（context_token 越可能仍有效）。"""
    if not text.strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    from .weixin_channel import WeixinChannelManager
    mgr = _WEIXIN_MGR or WeixinChannelManager(agent_fn=_weixin_noop_agent)
    fresh = fresh_minutes * 60 if fresh_minutes and fresh_minutes > 0 else None
    res = await mgr.quasi_push(text.strip(), fresh_within_seconds=fresh)
    return {"ok": True, **res}


def _format_news_for_push(msgs: list) -> str:
    """把若干条快讯渲染成推送文本（共享格式：每条 `MM-DD HH:MM  内容`）。"""
    from . import weixin_ilink
    return weixin_ilink.format_news_push(msgs)


@app.post("/api/weixin/push-news")
async def api_weixin_push_news(request: Request) -> dict[str, Any]:
    """微信推送台后端:按选中 ids 或最新 N 条快讯,标准格式 → quasi_push。body:{token, ids?[], latest?}。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not _admin_token_ok(request, str(body.get("token") or "")):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    from .realtime_messages import list_realtime_messages
    ids = [str(x) for x in (body.get("ids") or [])]
    latest = int(body.get("latest") or 0)
    if ids:
        idset = set(ids)
        msgs = [m for m in list_realtime_messages(topic="快讯", limit=200) if m.id in idset]
    elif latest > 0:
        msgs = list_realtime_messages(topic="快讯", limit=min(latest, 10))
    else:
        raise HTTPException(status_code=400, detail="需提供 ids 或 latest")
    if not msgs:
        return {"ok": False, "reason": "无匹配快讯"}
    text = _format_news_for_push(msgs)
    from .weixin_channel import WeixinChannelManager
    mgr = _WEIXIN_MGR or WeixinChannelManager(agent_fn=_weixin_noop_agent)
    res = await mgr.quasi_push(text)
    return {"ok": True, "preview": text, "count": len(msgs), **res}


@app.post("/api/weixin/push-config")
async def api_weixin_push_config(request: Request) -> dict[str, Any]:
    """设置某绑定的自动推送订阅：{token, deepfocus_user_id, scope(off/watchlist/all), symbols[]}。
    单绑定时 deepfocus_user_id 可省。新快讯到达按 symbols 匹配自动推（见 weixin_channel._auto_push_loop）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not _admin_token_ok(request, str(body.get("token") or "")):
        raise HTTPException(status_code=403, detail="需要有效的管理令牌")
    from . import weixin_bind
    uid = str(body.get("deepfocus_user_id") or "").strip()
    if not uid:
        actives = weixin_bind.list_active()
        if len(actives) == 1:
            uid = actives[0]["deepfocus_user_id"]
        else:
            raise HTTPException(status_code=400, detail="需指定 deepfocus_user_id")
    rec = weixin_bind.set_push_config(uid, str(body.get("scope") or "off"), list(body.get("symbols") or []))
    if not rec:
        return {"ok": False, "detail": "绑定不存在"}
    return {"ok": True, "scope": rec.get("push_scope"), "symbols": rec.get("push_symbols")}


@app.get("/api/weixin/console", response_class=HTMLResponse)
async def weixin_push_console(request: Request, token: str = "") -> HTMLResponse:
    """微信推送台(管理员):①按标的订阅自动推 ②手动立即推。需 ?token=管理令牌。仅内测渠道。"""
    if not _admin_token_ok(request, token):
        return HTMLResponse("<meta charset=utf-8><h3 style='font-family:sans-serif'>无权限：URL 后加 ?token=管理令牌</h3>", status_code=403)
    import html as _html
    from datetime import datetime as _d, timezone as _tz2, timedelta as _td2
    from . import weixin_bind
    from .realtime_messages import list_realtime_messages
    _CN = _tz2(_td2(hours=8))

    def _t(iso):
        try:
            return _d.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(_CN).strftime("%m-%d %H:%M")
        except Exception:
            return ""

    def _chk(cur, v):
        return " checked" if (cur or "off") == v else ""

    bindings = weixin_bind.list_active()
    cards = "".join(
        f'''<div class=card>
<div class=who>🟢 {_html.escape(b.get("deepfocus_user_id") or "")} <span class=mut>· {_html.escape((b.get("wechat_user_id") or "")[:10])}…</span></div>
<label class=opt><input type=radio name="sc_{i}" value=off{_chk(b.get("push_scope"), "off")}> 关闭推送</label>
<label class=opt><input type=radio name="sc_{i}" value=watchlist{_chk(b.get("push_scope"), "watchlist")}> 只推我选的标的</label>
<label class=opt><input type=radio name="sc_{i}" value=all{_chk(b.get("push_scope"), "all")}> 全部新快讯 <span class=warn>⚠️ 高频易封号</span></label>
<input class=syms id="sy_{i}" value="{_html.escape(", ".join(b.get("push_symbols") or []))}" placeholder="关注标的：宁德时代, 腾讯, 300750（逗号分隔，名称或代码）">
<button onclick="saveCfg('{_html.escape(b.get("deepfocus_user_id") or "")}',{i})">保存订阅</button>
</div>'''
        for i, b in enumerate(bindings)
    ) or '<div class=mut>暂无绑定（先在 App 账户菜单「绑定微信」扫码）</div>'

    msgs = list_realtime_messages(topic="快讯", limit=30)
    rows = "".join(
        f'<label class=row><input type=checkbox value="{_html.escape(m.id)}"><span class=tm>{_t(getattr(m, "created_at", ""))}</span>{_html.escape((m.title or "")[:70])}</label>'
        for m in msgs
    ) or '<div class=mut>暂无快讯</div>'

    # ③ 投递日志：自动推/保活的每次结果，让"断联"有据可查（服务端快照，刷新更新）
    _events = weixin_bind.recent_push_events(80)
    _sty = {"delivered": "#3fae6b", "failed": "#e0574f", "skipped": "#e0a23c", "dead": "#c2554f", "recovered": "#3fae6b"}
    _cnt: dict[str, int] = {}
    for _e in _events:
        _cnt[_e.get("status") or "?"] = _cnt.get(_e.get("status") or "?", 0) + 1
    log_summary = (" · ".join(f'{k}:{v}' for k, v in _cnt.items())) or "暂无记录（重启后还没有推送/保活事件）"
    log_rows = "".join(
        f'<div class=lrow><span class=tm>{_t(_e.get("ts"))}</span>'
        f'<span class=tag style="color:{_sty.get(_e.get("status") or "", "#9aa6ba")}">{_html.escape(_e.get("status") or "")}</span>'
        f'<span class=mut>{_html.escape(_e.get("username") or _e.get("bot_id") or "")}</span>'
        f'<span class=det>{_html.escape((_e.get("detail") or "")[:60])}</span></div>'
        for _e in _events
    ) or '<div class=mut>暂无投递记录。有新快讯命中订阅、或保活判定失效时会出现在这里。</div>'

    page = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>微信推送台</title><style>
body{font-family:-apple-system,system-ui,sans-serif;max-width:760px;margin:0 auto;padding:16px;background:#0e1320;color:#d7dde8}
h2{font-size:17px}h3{font-size:14px;color:#9aa6ba;margin:18px 0 8px}
button{background:#2b6cff;color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer}button.sec{background:#26314a}
input[type=number]{width:52px}input[type=text],.syms{width:100%;box-sizing:border-box;padding:8px;margin:8px 0;border-radius:6px;border:1px solid #283042;background:#0b0f1a;color:#d7dde8;font-size:13px}
.card{border:1px solid #283042;border-radius:10px;padding:12px 14px;margin-bottom:10px}
.who{font-size:14px;margin-bottom:8px}.opt{display:block;padding:4px 0;font-size:13px;cursor:pointer}.warn{color:#e0a23c;font-size:12px}
.row{display:flex;gap:8px;align-items:center;padding:7px 4px;border-bottom:1px solid #1c2333;font-size:13px}
.tm{color:#7f8aa3;font-variant-numeric:tabular-nums;flex:none;width:84px}.mut{color:#7f8aa3}
.lrow{display:flex;gap:8px;align-items:baseline;padding:5px 4px;border-bottom:1px solid #1c2333;font-size:12px}
.tag{flex:none;width:72px;font-weight:600}.det{color:#9aa6ba;flex:1;min-width:0}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
#res{padding:10px;margin-top:10px;border-radius:8px;background:#13351f;border:1px solid #1f5b34;white-space:pre-wrap;font-size:12px;display:none}#res.err{background:#3a1414;border-color:#5b1f1f}
details{margin-top:18px}summary{cursor:pointer;color:#9aa6ba;font-size:14px}
</style>
<h2>📢 DeepFocus 微信推送台 <small style="color:#7f8aa3;font-size:12px">· 仅内测渠道</small></h2>
<h3>① 按标的自动推送（选标的 → 有新快讯命中就自动推）</h3>
<div id=subs>__CARDS__</div>
<div id=res></div>
<details><summary>② 手动立即推（可选）</summary>
<div class=bar><button onclick=pushSel()>推送选中</button><button class=sec onclick=pushLatest()>推送最新 <input id=n type=number value=5 min=1 max=10> 条</button><button class=sec onclick="document.querySelectorAll('#list input[type=checkbox]').forEach(c=>c.checked=false)">清空</button></div>
<div id=list>__ROWS__</div>
</details>
<h3>③ 投递日志 <button class=sec style="padding:4px 10px;font-size:12px" onclick="location.reload()">刷新</button></h3>
<div class=mut style="font-size:12px;margin-bottom:6px">最近 80 条 · __LOGSUM__</div>
<div id=log>__LOG__</div>
<script>
var TK=__TOKEN__;
function show(ok,t){var r=document.getElementById('res');r.style.display='block';r.className=ok?'':'err';r.textContent=t;r.scrollIntoView({block:'nearest'})}
async function saveCfg(user,i){
  var el=document.querySelector('input[name=sc_'+i+']:checked');var scope=el?el.value:'off';
  var syms=(document.getElementById('sy_'+i).value||'').split(/[,，]/).map(function(s){return s.trim()}).filter(Boolean);
  show(true,'保存中…');
  try{var r=await fetch('/api/weixin/push-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TK,deepfocus_user_id:user,scope:scope,symbols:syms})});
  var d=await r.json();
  if(d&&d.ok===true){show(true,'✅ 订阅已保存：'+(d.scope||scope)+((d.scope||scope)==='watchlist'&&syms.length?('（'+syms.join(' / ')+'）'):'')+'\\n有命中的新快讯会自动推到你微信（仅对近期活跃有效）。')}
  else{show(false,'⚠️ '+(d.detail||'保存失败'))}}catch(e){show(false,'请求失败: '+e)}
}
async function call(p){show(true,'推送中…');try{var r=await fetch('/api/weixin/push-news',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({token:TK},p))});var d=await r.json();if(d.ok){show(true,'✅ 已推送 '+d.count+' 条 (delivered:'+d.delivered+' skipped:'+d.skipped+' failed:'+d.failed+')\\n\\n预览:\\n'+d.preview)}else{show(false,'⚠️ '+(d.reason||d.detail||'失败'))}}catch(e){show(false,'请求失败: '+e)}}
function pushSel(){var ids=[].slice.call(document.querySelectorAll('#list input[type=checkbox]:checked')).map(function(c){return c.value});if(!ids.length)return show(false,'未选中任何快讯');call({ids:ids})}
function pushLatest(){call({latest:parseInt(document.getElementById('n').value||'5')})}
</script>"""
    page = (page.replace("__CARDS__", cards).replace("__ROWS__", rows)
            .replace("__LOG__", log_rows).replace("__LOGSUM__", _html.escape(log_summary))
            .replace("__TOKEN__", json.dumps(token)))
    return HTMLResponse(page)


@app.get("/api/realtime/recall/deliveries", response_model=list[RecallDeliveryResult])
async def api_recent_recall_deliveries() -> list[RecallDeliveryResult]:
    return recent_deliveries()


@app.get("/api/realtime/recall/delivery-log", response_model=RecallDeliveryLogResponse)
async def api_recall_delivery_log(limit: int = 50) -> RecallDeliveryLogResponse:
    return RecallDeliveryLogResponse(deliveries=list_deliveries(limit))


@app.get("/api/realtime/recall/metrics", response_model=RecallMetricsResponse)
async def api_recall_metrics() -> RecallMetricsResponse:
    """召回闭环验真：送达 / 点击回流 / 回流率。"""
    return recall_metrics()


@app.get("/api/realtime/recall/click/{delivery_id}", include_in_schema=False)
async def api_recall_click(delivery_id: str) -> RedirectResponse:
    """召回点击回流追踪：记录点击并 302 跳回 App 深链（公开端点，用户从邮件/推送点回）。"""
    return RedirectResponse(url=resolve_recall_click(delivery_id), status_code=302)


@app.post("/api/share/snapshots", response_model=ShareSnapshotRecord)
async def api_create_share_snapshot(request: ShareSnapshotCreateRequest) -> ShareSnapshotRecord:
    return create_share_snapshot(request)


@app.get("/api/share/snapshots/{snapshot_id}", response_model=ShareSnapshotRecord)
async def api_get_share_snapshot(snapshot_id: str) -> ShareSnapshotRecord:
    record = get_share_snapshot(snapshot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="分享快照不存在")
    return record


@app.get("/s/{snapshot_id}", response_class=HTMLResponse)
async def public_share_page(snapshot_id: str, request: Request) -> HTMLResponse:
    """免登录、可被搜索引擎收录的只读结论页（服务端直出 HTML）。"""
    record = get_share_snapshot(snapshot_id)
    if record is None:
        return HTMLResponse(render_not_found_html(), status_code=404)
    increment_share_views(snapshot_id)
    return HTMLResponse(render_share_page_html(record, page_url=str(request.url)))


# --------------------------------------------------------------------------- #
# 公开 SEO 落地页（曝光增长）：复盘页 / 个股速判页 / 聚合页 / 站点地图。
# 非 /api/ 路径，鉴权中间件天然放行；生产 nginx 需把这些 location 代理到后端。
# --------------------------------------------------------------------------- #

# 个股页防爬虫打爆：速判卡重建要并发十余个外源，公开页限并发 + 结果落 data_store 复用 1h。
_SEO_TS_TTL = 3600.0
_seo_build_semaphore = asyncio.Semaphore(2)
_SEO_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")


@app.get("/robots.txt", include_in_schema=False)
async def public_robots() -> Response:
    return Response(seo_pages.render_robots_txt(), media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def public_sitemap() -> Response:
    dates: list[str] = []
    seen: set[str] = set()
    for it in ashare_review.list_reviews(limit=120):
        d = it.get("date") or ""
        if d and d not in seen:
            seen.add(d)
            dates.append(d)
    symbols = [h["symbol"] for h in data_hot_symbols("verdict", days=90, limit=100)]
    return Response(seo_pages.render_sitemap_xml(dates, symbols), media_type="application/xml")


@app.get("/review", response_class=HTMLResponse, include_in_schema=False)
async def public_review_hub() -> HTMLResponse:
    return HTMLResponse(seo_pages.render_review_hub_html(ashare_review.list_reviews(limit=90)))


@app.get("/review/{date_str}", response_class=HTMLResponse, include_in_schema=False)
async def public_review_page(date_str: str, request: Request) -> HTMLResponse:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str or ""):
        return HTMLResponse(render_not_found_html(), status_code=404)
    review = ashare_review.review_for_date(date_str)
    if not review:
        return HTMLResponse(render_not_found_html(), status_code=404)
    recent = ashare_review.list_reviews(limit=12)
    return HTMLResponse(seo_pages.render_review_page_html(review, recent, page_url=str(request.url)))


def _seo_related_stocks(symbol: str, market: str) -> list[dict[str, Any]]:
    """「大家也在看」：同市场近期热门优先，不足补全局热门（站内推荐 + SEO 内链）。"""
    related = data_hot_symbols("verdict", market=market or None, days=30, limit=8, exclude=symbol)
    if len(related) < 8:
        seen = {r["symbol"] for r in related} | {symbol}
        related += [r for r in data_hot_symbols("verdict", days=30, limit=16, exclude=symbol) if r["symbol"] not in seen][: 8 - len(related)]
    return related


@app.get("/stocks", response_class=HTMLResponse, include_in_schema=False)
async def public_stocks_hub() -> HTMLResponse:
    items = []
    for h in data_hot_symbols("verdict", days=30, limit=50):
        v = data_latest("verdict", h["symbol"]) or {}
        items.append({**h, "verdict": v.get("verdict"), "change_percent": v.get("change_percent")})
    return HTMLResponse(seo_pages.render_stocks_hub_html(items))


@app.get("/stock/{symbol}", response_class=HTMLResponse, include_in_schema=False)
async def public_stock_page(symbol: str, request: Request, market: str = "") -> HTMLResponse:
    sym = (symbol or "").strip().upper()
    if not _SEO_SYMBOL_RE.match(sym):
        return HTMLResponse(render_not_found_html(), status_code=404)
    ts_dict = data_latest("seo_tear_sheet", sym, max_age_seconds=_SEO_TS_TTL)
    if not ts_dict:
        async with _seo_build_semaphore:
            ts_dict = data_latest("seo_tear_sheet", sym, max_age_seconds=_SEO_TS_TTL)  # 排队期间可能已被同标的请求建好
            if not ts_dict:
                try:
                    ts = await _build_stock_tear_sheet_core(sym, market=market)
                except Exception:
                    return HTMLResponse(seo_pages.render_error_html(), status_code=503)
                ts_dict = ts.model_dump(mode="json")
                for heavy in ("price_series", "sp500_series", "us10y_series"):  # 页面不画图，不存大序列
                    ts_dict.pop(heavy, None)
                record_datapoint("seo_tear_sheet", sym, ts_dict, market=(market or "").upper())
    record_datapoint("seo_view", sym, {"path": "stock"}, market=(market or "").upper())  # 页面热度（推荐/榜单信号）
    related = _seo_related_stocks(sym, (market or "").upper())
    return HTMLResponse(seo_pages.render_stock_page_html(ts_dict, related, page_url=str(request.url)))


@app.get("/api/mcp/servers", response_model=McpServerListResponse)
async def api_list_mcp_servers() -> McpServerListResponse:
    return McpServerListResponse(servers=list_mcp_servers())


@app.post("/api/mcp/servers", response_model=McpServerRecord)
async def api_create_mcp_server(request: McpServerCreateRequest) -> McpServerRecord:
    return create_mcp_server(request)


@app.delete("/api/mcp/servers/{server_id}")
async def api_delete_mcp_server(server_id: str) -> dict[str, bool]:
    if not delete_mcp_server(server_id):
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"ok": True}


@app.post("/api/mcp/servers/{server_id}/discover", response_model=McpDiscoverResponse)
async def api_discover_mcp_server(server_id: str) -> McpDiscoverResponse:
    server, capabilities, warnings = await discover_mcp_server(server_id)
    return McpDiscoverResponse(server=server, capabilities=capabilities, warnings=warnings)


@app.get("/api/mcp/capabilities", response_model=McpCapabilityListResponse)
async def api_list_mcp_capabilities(
    server_id: Optional[str] = None,
    capability_type: Optional[str] = None,
) -> McpCapabilityListResponse:
    return McpCapabilityListResponse(
        capabilities=list_mcp_capabilities(server_id=server_id, capability_type=capability_type)
    )


@app.post("/api/mcp/servers/{server_id}/tools/call", response_model=McpToolCallResponse)
async def api_call_mcp_tool(server_id: str, request: McpToolCallRequest) -> McpToolCallResponse:
    return await call_mcp_tool(server_id, request)


def _format_interpretation(result: FinGptTaskResponse) -> str:
    def block(title: str, lines: list[str]) -> str:
        clean_lines = [line.strip() for line in lines if line.strip()]
        if not clean_lines:
            return ""
        return f"{title}\n" + "\n".join(f"- {line}" for line in clean_lines)

    capability_label = {
        "wechat_article": "公众号事件快读",
        "report_analysis": "财报/研报解读",
        "news_summary": "新闻蒸馏",
    }.get(result.capability, result.capability)
    parts = [
        f"标题：{result.title}",
        f"能力：{capability_label}",
        f"生成时间：{result.generated_at.isoformat()}",
        f"模型：{result.provider} / {result.model}",
        "",
        f"摘要：{result.summary}",
        "",
        block("核心要点", result.key_points),
        "",
        block("信号", result.signals),
        "",
        block("风险", result.risks),
        "",
        block("后续动作", result.actions),
        "",
        block("证据来源", result.sources),
        "",
        result.disclaimer,
    ]
    return "\n".join(part for part in parts if part != "")


def _is_wechat_public_item(item: DataSourceItemRecord) -> bool:
    tags = {tag.lower() for tag in item.tags}
    provider = str(item.metadata.get("provider") or "").lower()
    return provider == "wechat_public" or "公众号" in item.tags or "搜狗微信" in item.tags or "wechat_public" in tags


def _wechat_article_payload(item: DataSourceItemRecord) -> dict[str, object]:
    return {
        "title": item.title,
        "summary": _extract_labeled_text(item.text, "摘要") or item.text_preview,
        "account": item.metadata.get("account") or _extract_labeled_text(item.text, "公众号") or item.source_name,
        "published": item.metadata.get("published") or _extract_labeled_text(item.text, "时间"),
        "published_at": item.metadata.get("published_at") or item.collected_at,
        "symbol": item.symbol,
        "keyword": _extract_labeled_text(item.text, "搜索关键词"),
        "tags": item.tags,
        "url": item.url,
    }


def _extract_labeled_text(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[：:]\s*(.*?)(?=\n\S{{1,16}}[：:]|\Z)", text or "", flags=re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:600]


async def _run_stock_check_job(key: str, name: str, coro: Any) -> tuple[str, StockCheckStep, Any]:
    try:
        value = await asyncio.wait_for(coro, timeout=18)
        return key, StockCheckStep(key=key, name=name, status="completed", detail="已完成"), value
    except Exception as exc:
        return key, StockCheckStep(key=key, name=name, status="failed", detail=_clean_step_error(exc)), None


def _mark_stock_check_fallback(checks: list[StockCheckStep], key: str) -> None:
    for step in checks:
        if step.key == key:
            step.status = "completed"
            step.detail = "云模型未及时返回，已使用本地规则兜底。"
            return


def _stock_check_context(
    request: StockCheckRequest,
    evidence_items: Optional[list[DataSourceItemRecord]] = None,
) -> str:
    stock = request.stock
    lines = [
        f"标的：{stock.name}（{stock.symbol}）",
        f"行业：{stock.sector or '未知'}",
        f"价格：{stock.current_price if stock.current_price is not None else '未知'}",
        f"涨跌幅：{stock.change_percent if stock.change_percent is not None else '未知'}%",
        f"市值：{stock.market_cap if stock.market_cap is not None else '未知'}",
        f"关注度：{stock.focus_level or '未知'}",
        f"社区热度：{stock.community_score if stock.community_score is not None else '未知'}",
        f"描述：{stock.description or ''}",
    ]
    if request.question:
        lines.append(f"用户关注：{request.question}")
    for index, post in enumerate(request.posts[:10], start=1):
        lines.extend(
            [
                "",
                f"资料 {index}：{post.title}",
                f"摘要：{post.summary or ''}",
                f"内容：{(post.content or '')[:900]}",
                f"标签：{', '.join(post.tags)}",
                f"时间：{post.publish_time or ''}",
            ]
        )
    for index, item in enumerate((evidence_items or [])[:8], start=1):
        lines.extend(
            [
                "",
                f"数据源 {index}：{item.title}",
                f"来源：{item.source_name}",
                f"可信度：{round(item.credibility_score * 100)}%",
                f"摘要：{item.text_preview[:700]}",
                f"标签：{', '.join(item.tags)}",
                f"时间：{item.collected_at}",
            ]
        )
    return "\n".join(lines)[:9000]


def _stock_check_documents(
    request: StockCheckRequest,
    evidence_items: Optional[list[DataSourceItemRecord]] = None,
) -> list[dict[str, str]]:
    documents = [
        {
            "source": "stock_snapshot",
            "text": _stock_check_context(
                StockCheckRequest(
                    stock=request.stock,
                    posts=[],
                    question=request.question,
                    horizon=request.horizon,
                    locale=request.locale,
                )
            ),
        }
    ]
    for post in request.posts[:8]:
        documents.append(
            {
                "source": post.title,
                "text": "\n".join([post.summary or "", (post.content or "")[:1200], ", ".join(post.tags)]).strip(),
            }
        )
    for item in (evidence_items or [])[:8]:
        documents.append(
            {
                "source": item.title,
                "text": "\n".join([item.text_preview, item.text[:1200], ", ".join(item.tags)]).strip(),
            }
        )
    return documents


async def _stock_check_single_pass(
    request: StockCheckRequest,
    context: str,
) -> Optional[StockCheckResponse]:
    if llm.provider == "mock":
        return None
    prompt = (
        "你是专业股票投研系统的 One-Click Stock Check 编排器。"
        "请在一次输出中同时完成这些 FinGPT 镜头：个股投研、金融情绪、新闻蒸馏、资料解读、RAG验证、预测推演、Agent复核。"
        "不要给确定性交易建议；要像投资者体检报告，指出值得跟踪的理由、风险、证据缺口和下一步动作。\n"
        "返回严格 JSON object，字段：verdict, score, confidence, summary, action_items, risk_flags, "
        "sentiment_label, sentiment_score, sentiment_rationale, stock_summary, catalysts, stock_risks, watch_items, "
        "sections。verdict 只能是 重点跟踪/谨慎观察/暂不行动；score 0-100；confidence 0-1。"
        "sections 是对象，必须包含 news_summary, report_analysis, rag_query, forecast, agent_brief；"
        "每个 section 字段为 summary, key_points, signals, risks, actions, sources, confidence。"
        "数组每项不超过 24 个中文字符，每个数组最多 5 项。\n"
        f"输入：{context}"
    )
    try:
        data = await asyncio.wait_for(
            llm.complete_json(prompt, max_tokens=2200, timeout_seconds=14, force_json_first=False),
            timeout=16,
        )
    except Exception as exc:
        return _stock_check_local_response(request, context, _clean_step_error(exc))

    verdict = str(data.get("verdict") or "谨慎观察")
    if verdict not in {"重点跟踪", "谨慎观察", "暂不行动"}:
        verdict = "谨慎观察"
    score = int(round(clamp(_number_value(data.get("score"), 50), 0, 100)))
    confidence = clamp(_number_value(data.get("confidence"), 0.6), 0, 1)
    label = str(data.get("sentiment_label") or "neutral").lower()
    if label not in {"positive", "neutral", "negative"}:
        label = "neutral"
    sentiment = SentimentResponse(
        provider=llm.provider_name,
        model=llm.model,
        label=label,
        score=clamp(_number_value(data.get("sentiment_score"), 0), -1, 1),
        rationale=str(data.get("sentiment_rationale") or "一键检测综合判断。"),
    )
    stock_analysis = StockAnalysisResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        executive_summary=str(data.get("stock_summary") or data.get("summary") or "已完成一键检测。"),
        sentiment_label=label,
        sentiment_score=sentiment.score,
        risk_level="high" if score < 45 else "medium" if score < 68 else "low",
        catalysts=_json_list(data.get("catalysts")),
        risks=_json_list(data.get("stock_risks") or data.get("risk_flags")),
        watch_items=_json_list(data.get("watch_items") or data.get("action_items")),
        suggested_questions=_json_list(data.get("suggested_questions")) or ["关键催化是否可验证？", "风险是否已反映在价格中？"],
    )
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}

    def section_task(key: str, title: str) -> FinGptTaskResponse:
        section = sections.get(key) if isinstance(sections.get(key), dict) else {}
        return FinGptTaskResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            capability=key,
            title=f"{request.stock.name} {title}",
            summary=str(section.get("summary") or data.get("summary") or "已完成检测。"),
            key_points=_json_list(section.get("key_points")),
            signals=_json_list(section.get("signals")),
            risks=_json_list(section.get("risks")),
            actions=_json_list(section.get("actions")),
            sources=_json_list(section.get("sources")) or ["一键检测输入"],
            confidence=clamp(_number_value(section.get("confidence"), confidence), 0, 1),
        )

    checks = [
        StockCheckStep(key="stock_analysis", name="个股投研", status="completed", detail="一键编排完成"),
        StockCheckStep(key="sentiment", name="金融情绪", status="completed", detail="一键编排完成"),
        StockCheckStep(key="news_summary", name="新闻蒸馏", status="completed", detail="一键编排完成"),
        StockCheckStep(key="report_analysis", name="资料解读", status="completed", detail="一键编排完成"),
        StockCheckStep(key="rag_answer", name="RAG问答", status="completed", detail="一键编排完成"),
        StockCheckStep(key="forecast", name="预测推演", status="completed", detail="一键编排完成"),
        StockCheckStep(key="agent_brief", name="Agent复核", status="completed", detail="一键编排完成"),
        StockCheckStep(key="corridor_risk", name="通道风险", status="skipped", detail="稳定币/支付通道风险不适用于普通个股一键检测。"),
    ]
    return StockCheckResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=confidence,
        summary=str(data.get("summary") or stock_analysis.executive_summary),
        action_items=_json_list(data.get("action_items")) or stock_analysis.watch_items,
        risk_flags=_json_list(data.get("risk_flags")) or stock_analysis.risks,
        checks=checks,
        stock_analysis=stock_analysis,
        sentiment=sentiment,
        news_summary=section_task("news_summary", "新闻蒸馏"),
        report_analysis=section_task("report_analysis", "资料解读"),
        rag_answer=section_task("rag_query", "RAG问答"),
        forecast=section_task("forecast", "预测推演"),
        agent_brief=section_task("agent_brief", "Agent复核"),
        warnings=[],
    )


def _stock_check_local_response(
    request: StockCheckRequest,
    context: str,
    reason: str,
) -> StockCheckResponse:
    stock_result = _fallback_stock_analysis(request)
    sentiment_result = _fallback_sentiment(context)
    news_result = _fallback_task("news_summary", "新闻蒸馏", request, context)
    report_result = _fallback_task("report_analysis", "资料解读", request, context)
    rag_result = _fallback_task("rag_query", "RAG问答", request, context)
    forecast_result = _fallback_task("forecast", "预测推演", request, context)
    agent_result = _fallback_task("agent_brief", "Agent复核", request, context)
    score = _stock_check_score(request, stock_result, sentiment_result, forecast_result)
    verdict = "重点跟踪" if score >= 68 else "谨慎观察" if score >= 45 else "暂不行动"
    checks = [
        StockCheckStep(key="stock_analysis", name="个股投研", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="sentiment", name="金融情绪", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="news_summary", name="新闻蒸馏", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="report_analysis", name="资料解读", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="rag_answer", name="RAG问答", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="forecast", name="预测推演", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="agent_brief", name="Agent复核", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="corridor_risk", name="通道风险", status="skipped", detail="稳定币/支付通道风险不适用于普通个股一键检测。"),
    ]
    return StockCheckResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=0.45,
        summary=_stock_check_summary(
            request,
            verdict,
            score,
            stock_result,
            sentiment_result,
            news_result,
            forecast_result,
            [],
        ),
        action_items=_dedupe_lines(stock_result.watch_items + forecast_result.actions + rag_result.actions + agent_result.actions)[:8],
        risk_flags=_dedupe_lines(stock_result.risks + forecast_result.risks + report_result.risks + news_result.risks)[:8],
        checks=checks,
        stock_analysis=stock_result,
        sentiment=sentiment_result,
        news_summary=news_result,
        report_analysis=report_result,
        rag_answer=rag_result,
        forecast=forecast_result,
        agent_brief=agent_result,
        warnings=[],
    )


def _stock_check_score(
    request: StockCheckRequest,
    stock_result: Optional[StockAnalysisResponse],
    sentiment_result: Optional[SentimentResponse],
    forecast_result: Optional[FinGptTaskResponse],
) -> int:
    score = 50.0
    if sentiment_result:
        score += sentiment_result.score * 18
    if stock_result:
        score += {"low": 8, "medium": 0, "high": -14}.get(stock_result.risk_level, 0)
        score += {"positive": 8, "neutral": 0, "negative": -8}.get(stock_result.sentiment_label, 0)
    if forecast_result:
        score += (forecast_result.confidence - 0.5) * 12
        bearish_words = ("下行", "承压", "回撤", "风险", "走弱")
        bullish_words = ("上行", "改善", "催化", "增长", "突破")
        joined = " ".join(forecast_result.signals + forecast_result.key_points + forecast_result.actions)
        score += 5 if any(word in joined for word in bullish_words) else 0
        score -= 5 if any(word in joined for word in bearish_words) else 0
    if request.stock.change_percent is not None:
        score += max(-6, min(6, request.stock.change_percent))
    return int(round(clamp(score, 0, 100)))


def _fallback_stock_analysis(request: StockCheckRequest) -> StockAnalysisResponse:
    sentiment_label, sentiment_score = _fallback_sentiment_label(_stock_check_context(request))
    risk_level = "medium"
    if request.stock.change_percent is not None and request.stock.change_percent <= -4:
        risk_level = "high"
    elif request.stock.focus_level == "high" and request.stock.community_score and request.stock.community_score >= 75:
        risk_level = "medium"
    return StockAnalysisResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        executive_summary=(
            f"{request.stock.name} 当前由本地规则完成快速体检：结合价格、关注度、社区资料和用户问题，"
            "先输出观察清单，等待云模型或外部数据补充验证。"
        ),
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        risk_level=risk_level,
        catalysts=_dedupe_lines([post.title for post in request.posts[:4]] + ["关注公告和财报更新"])[:4],
        risks=["云模型未完成，结论需复核", "资料可能不完整", "短线价格波动可能放大"],
        watch_items=["补充最新公告", "核对财报关键指标", "跟踪成交量和资金流", "复核估值与催化匹配"],
        suggested_questions=["最新催化是否可验证？", "主要风险是否已反映在价格中？"],
    )


def _fallback_sentiment(text: str) -> SentimentResponse:
    label, score = _fallback_sentiment_label(text)
    return SentimentResponse(
        provider="local-rule",
        model="stock-check-v1",
        label=label,
        score=score,
        rationale="云模型未及时返回，使用本地关键词和风险词做快速情绪判断。",
    )


def _fallback_task(
    capability: str,
    title: str,
    request: StockCheckRequest,
    context: str,
) -> FinGptTaskResponse:
    label, _ = _fallback_sentiment_label(context)
    stock_name = f"{request.stock.name}（{request.stock.symbol}）"
    signals = _dedupe_lines(
        [post.title for post in request.posts[:3]]
        + _extract_context_titles(context)
        + [f"{stock_name} 关注度：{request.stock.focus_level or '未知'}"]
    )
    risks = ["云模型未及时返回，需人工复核", "数据源覆盖可能不足", "短线信号不能替代基本面"]
    actions = ["补充最新公告/财报", "交叉验证新闻来源", "跟踪价格和成交量"]
    if label == "positive":
        signals.append("本地情绪偏积极")
    elif label == "negative":
        risks.append("本地情绪偏谨慎")
    return FinGptTaskResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        capability=capability,
        title=f"{stock_name} {title}",
        summary=f"{title}云模型未及时返回，已基于本地资料生成快速复核清单。",
        key_points=signals[:5],
        signals=signals[:5],
        risks=risks[:5],
        actions=actions,
        sources=["本地个股快照", "社区/资料输入"],
        confidence=0.45,
    )


def _fallback_sentiment_label(text: str) -> tuple[str, float]:
    lowered = text.lower()
    positive = sum(1 for word in ["增长", "超预期", "改善", "上调", "催化", "突破", "positive", "beat"] if word in lowered)
    negative = sum(1 for word in ["风险", "下滑", "承压", "监管", "事故", "不确定", "negative", "miss"] if word in lowered)
    if positive > negative:
        return "positive", min(0.8, 0.2 + positive * 0.12)
    if negative > positive:
        return "negative", max(-0.8, -0.2 - negative * 0.12)
    return "neutral", 0.0


def _extract_context_titles(context: str) -> list[str]:
    titles = re.findall(r"(?:资料|数据源)\s*\d+[：:]\s*(.+)", context or "")
    return [title.strip() for title in titles[:6] if title.strip()]


def _stock_check_summary(
    request: StockCheckRequest,
    verdict: str,
    score: int,
    stock_result: Optional[StockAnalysisResponse],
    sentiment_result: Optional[SentimentResponse],
    news_result: Optional[FinGptTaskResponse],
    forecast_result: Optional[FinGptTaskResponse],
    warnings: list[str],
) -> str:
    parts = [
        f"{request.stock.name}（{request.stock.symbol}）一键检测结论为“{verdict}”，综合分 {score}/100。",
    ]
    if stock_result:
        parts.append(stock_result.executive_summary)
    elif news_result:
        parts.append(news_result.summary)
    if sentiment_result:
        parts.append(f"文本情绪为{sentiment_result.label}，情绪分 {sentiment_result.score:.2f}。")
    if forecast_result:
        parts.append(f"预测推演：{forecast_result.summary}")
    if warnings:
        parts.append(f"有 {len(warnings)} 个能力未完成，需人工复核。")
    return " ".join(part.strip() for part in parts if part.strip())[:900]


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        clean = re.sub(r"\s+", " ", str(line or "")).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean[:120])
    return result


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe_lines([str(item) for item in value])[:6]
    if isinstance(value, str) and value.strip():
        return _dedupe_lines(re.split(r"[；;\n]", value))[:6]
    return []


def _number_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_step_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return text[:260] or exc.__class__.__name__


@app.get("/api/data-sources/{source_id}", response_model=DataSourceRecord)
async def api_get_data_source(source_id: str) -> DataSourceRecord:
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return source


@app.get("/api/data/stats")
async def data_store_stats() -> dict:
    """持久化数据层沉淀概览：每类数据点数 / 覆盖标的 / 最新时间（数据积累的观测性入口）。"""
    return data_stats()


@app.get("/api/data/history")
async def data_store_history(symbol: str, kind: str = "verdict", limit: int = 200) -> dict:
    """某标的某类数据的历史（新→旧）。kind 默认 verdict（速判卡结论随时间的演变）。"""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    return {"symbol": sym, "kind": kind, "items": data_history(kind, sym, limit=limit)}


@app.get("/api/agents/health", response_model=AgentRuntimeHealthResponse)
async def agent_runtime_health() -> AgentRuntimeHealthResponse:
    counts = task_counts()
    return AgentRuntimeHealthResponse(
        status="ok",
        worker_running=is_worker_running(),
        pending=counts["pending"],
        running=counts["running"],
        completed=counts["completed"],
        failed=counts["failed"],
    )


@app.get("/api/agents/engines", response_model=AgentEngineListResponse)
async def list_agent_engines() -> AgentEngineListResponse:
    """自描述：注册表里有什么引擎，API 就返回什么。新增引擎自动出现，前端无需硬编码。"""
    return AgentEngineListResponse(
        default=DEFAULT_ENGINE_KEY,
        engines=[
            AgentEngineInfo(
                key=spec.key,
                label=spec.label,
                description=spec.description,
                stage_count=len(spec.stages),
            )
            for spec in list_engines()
        ],
    )


@app.get("/api/agents/tools", response_model=AgentToolListResponse)
async def list_agent_tools_endpoint() -> AgentToolListResponse:
    """自描述：tool-use agent 能调用的工具清单（内部数据工具 + 已接入的外部 MCP 工具）+ 该路径是否启用。"""
    tools = list(list_agent_tool_specs())
    try:
        tools = tools + await discover_mcp_agent_tools()  # best-effort：MCP 不可用不影响内部工具列示
    except Exception:
        pass
    return AgentToolListResponse(
        enabled=_tool_agent_enabled(),
        tools=[
            AgentToolInfo(
                name=tool.name,
                description=tool.description,
                parameters=list((tool.parameters.get("properties") or {}).keys()),
            )
            for tool in tools
        ],
    )


@app.get("/api/agents/tasks", response_model=InvestmentTaskListResponse)
async def list_agent_tasks(limit: int = 50) -> InvestmentTaskListResponse:
    return InvestmentTaskListResponse(tasks=list_investment_tasks(limit=limit))


@app.post("/api/agents/tasks", response_model=InvestmentTaskRecord)
async def create_agent_task(request: InvestmentTaskCreateRequest) -> InvestmentTaskRecord:
    return create_investment_task(request)


@app.get("/api/agents/tasks/{task_id}", response_model=InvestmentTaskRecord)
async def get_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = get_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/agents/tasks/{task_id}/events")
async def stream_agent_task_events(task_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        agent_task_event_stream(task_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/agents/tasks/{task_id}/retry", response_model=InvestmentTaskRecord)
async def retry_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = retry_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/agents/tasks/{task_id}/cancel", response_model=InvestmentTaskRecord)
async def cancel_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = cancel_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/dulus/status", response_model=DulusRuntimeStatusResponse)
async def dulus_status() -> DulusRuntimeStatusResponse:
    return build_dulus_runtime_status(llm)


@app.get("/api/dulus/tools", response_model=list[DulusToolRecord])
async def dulus_tools() -> list[DulusToolRecord]:
    return list_dulus_tools()


@app.get("/api/dulus/memory", response_model=DulusMemoryListResponse)
async def dulus_memory(limit: int = 20, scope: Optional[str] = None) -> DulusMemoryListResponse:
    return list_dulus_memories(limit=limit, scope=scope)


@app.post("/api/dulus/memory", response_model=DulusMemoryRecord)
async def dulus_memory_create(request: DulusMemoryCreateRequest) -> DulusMemoryRecord:
    return create_dulus_memory(request)


@app.post("/api/dulus/webbridge/inspect", response_model=DulusWebBridgeInspectResponse)
async def dulus_webbridge_inspect(request: DulusWebBridgeInspectRequest) -> DulusWebBridgeInspectResponse:
    return inspect_authorized_webbridge(request)


@app.post("/api/dulus/roundtable", response_model=DulusRoundtableResponse)
async def dulus_roundtable(request: DulusRoundtableRequest) -> DulusRoundtableResponse:
    try:
        return await run_dulus_roundtable(llm, request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/stock-analysis", response_model=StockAnalysisResponse)
async def stock_analysis(request: StockAnalysisRequest) -> StockAnalysisResponse:
    try:
        return attach_data_quality(await llm.analyze_stock(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/sentiment", response_model=SentimentResponse)
async def sentiment(request: SentimentRequest) -> SentimentResponse:
    try:
        return attach_data_quality(await llm.score_sentiment(request.text))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/stock-check", response_model=StockCheckResponse)
async def stock_check(request: StockCheckRequest) -> StockCheckResponse:
    posts = request.posts[:10]
    evidence_items = list_data_items(symbol=request.stock.symbol, limit=8, sort="time_desc")
    stock_context = _stock_check_context(request, evidence_items)
    single_pass = await _stock_check_single_pass(request, stock_context)
    if single_pass:
        return single_pass

    news_items = [
        {
            "title": post.title,
            "summary": post.summary,
            "source": post.category,
            "published_at": post.publish_time,
        }
        for post in posts
    ]
    documents = _stock_check_documents(request, evidence_items)

    jobs = {
        "stock_analysis": (
            "个股投研",
            llm.analyze_stock(
                StockAnalysisRequest(
                    stock=request.stock,
                    posts=posts,
                    question=request.question,
                    locale=request.locale,
                )
            ),
        ),
        "sentiment": (
            "金融情绪",
            llm.score_sentiment(stock_context),
        ),
        "news_summary": (
            "新闻蒸馏",
            llm.summarize_news(
                NewsSummaryRequest(
                    stock=request.stock,
                    items=news_items,
                    focus=request.question or "识别影响股价的事实变化、催化和风险",
                    locale=request.locale,
                )
            ),
        ),
        "report_analysis": (
            "资料解读",
            llm.analyze_report(
                ReportAnalysisRequest(
                    title=f"{request.stock.name} 一键检测资料解读",
                    report_text=stock_context,
                    stock=request.stock,
                    locale=request.locale,
                )
            ),
        ),
        "rag_answer": (
            "RAG问答",
            llm.rag_query(
                RagQueryRequest(
                    question=f"基于资料，{request.stock.name}（{request.stock.symbol}）当前最需要验证的投资问题是什么？",
                    documents=documents,
                    locale=request.locale,
                )
            ),
        ),
        "forecast": (
            "预测推演",
            llm.forecast(
                ForecastRequest(
                    stock=request.stock,
                    horizon=request.horizon,
                    context=request.question,
                    posts=posts,
                    locale=request.locale,
                )
            ),
        ),
        "agent_brief": (
            "Agent复核",
            llm.agent_brief(
                AgentBriefRequest(
                    role="ops_oversight",
                    context=(
                        f"请作为投研流程监督 Agent，复核 {request.stock.name}（{request.stock.symbol}）"
                        f"的一键检测输入，输出需要人工复核的事实、风险和下一步动作。\n{stock_context[:5000]}"
                    ),
                    locale=request.locale,
                )
            ),
        ),
    }
    results = await asyncio.gather(
        *[_run_stock_check_job(key, name, coro) for key, (name, coro) in jobs.items()]
    )
    data = {key: value for key, _, value in results}
    checks = [step for _, step, _ in results]
    checks.append(
        StockCheckStep(
            key="corridor_risk",
            name="通道风险",
            status="skipped",
            detail="稳定币/支付通道风险不适用于普通个股一键检测。",
        )
    )

    stock_result = data.get("stock_analysis") if isinstance(data.get("stock_analysis"), StockAnalysisResponse) else None
    sentiment_result = data.get("sentiment") if isinstance(data.get("sentiment"), SentimentResponse) else None
    news_result = data.get("news_summary") if isinstance(data.get("news_summary"), FinGptTaskResponse) else None
    report_result = data.get("report_analysis") if isinstance(data.get("report_analysis"), FinGptTaskResponse) else None
    rag_result = data.get("rag_answer") if isinstance(data.get("rag_answer"), FinGptTaskResponse) else None
    forecast_result = data.get("forecast") if isinstance(data.get("forecast"), FinGptTaskResponse) else None
    agent_result = data.get("agent_brief") if isinstance(data.get("agent_brief"), FinGptTaskResponse) else None

    if stock_result is None:
        stock_result = _fallback_stock_analysis(request)
        _mark_stock_check_fallback(checks, "stock_analysis")
    if sentiment_result is None:
        sentiment_result = _fallback_sentiment(stock_context)
        _mark_stock_check_fallback(checks, "sentiment")
    if news_result is None:
        news_result = _fallback_task("news_summary", "新闻蒸馏", request, stock_context)
        _mark_stock_check_fallback(checks, "news_summary")
    if report_result is None:
        report_result = _fallback_task("report_analysis", "资料解读", request, stock_context)
        _mark_stock_check_fallback(checks, "report_analysis")
    if rag_result is None:
        rag_result = _fallback_task("rag_query", "RAG问答", request, stock_context)
        _mark_stock_check_fallback(checks, "rag_answer")
    if forecast_result is None:
        forecast_result = _fallback_task("forecast", "预测推演", request, stock_context)
        _mark_stock_check_fallback(checks, "forecast")
    if agent_result is None:
        agent_result = _fallback_task("agent_brief", "Agent复核", request, stock_context)
        _mark_stock_check_fallback(checks, "agent_brief")

    warnings = [step.detail for step in checks if step.status == "failed"]
    score = _stock_check_score(request, stock_result, sentiment_result, forecast_result)
    verdict = "重点跟踪" if score >= 68 else "谨慎观察" if score >= 45 else "暂不行动"
    task_confidences = [
        item.confidence
        for item in [news_result, report_result, rag_result, forecast_result, agent_result]
        if item is not None
    ]
    confidence = clamp(
        (sum(task_confidences) / len(task_confidences)) if task_confidences else 0.55,
        0,
        1,
    )

    action_items = _dedupe_lines(
        (stock_result.watch_items if stock_result else [])
        + (forecast_result.actions if forecast_result else [])
        + (rag_result.actions if rag_result else [])
        + (agent_result.actions if agent_result else [])
    )[:8]
    risk_flags = _dedupe_lines(
        (stock_result.risks if stock_result else [])
        + (forecast_result.risks if forecast_result else [])
        + (report_result.risks if report_result else [])
        + (news_result.risks if news_result else [])
    )[:8]

    summary = _stock_check_summary(
        request,
        verdict,
        score,
        stock_result,
        sentiment_result,
        news_result,
        forecast_result,
        warnings,
    )
    return StockCheckResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=confidence,
        summary=summary,
        action_items=action_items,
        risk_flags=risk_flags,
        checks=checks,
        stock_analysis=stock_result,
        sentiment=sentiment_result,
        news_summary=news_result,
        report_analysis=report_result,
        rag_answer=rag_result,
        forecast=forecast_result,
        agent_brief=agent_result,
        warnings=warnings,
    )


@app.post("/api/fingpt/news-summary", response_model=FinGptTaskResponse)
async def news_summary(request: NewsSummaryRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.summarize_news(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/report-analysis", response_model=FinGptTaskResponse)
async def report_analysis(request: ReportAnalysisRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.analyze_report(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/rag-query", response_model=FinGptTaskResponse)
async def rag_query(request: RagQueryRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.rag_query(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/forecast", response_model=FinGptTaskResponse)
async def forecast(request: ForecastRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.forecast(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/corridor-risk", response_model=FinGptTaskResponse)
async def corridor_risk(request: CorridorRiskRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.corridor_risk(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/agent-brief", response_model=FinGptTaskResponse)
async def agent_brief(request: AgentBriefRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.agent_brief(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _crawl_keyword_for_context(ctx: dict) -> Optional[str]:
    """取一个适合搜索的关键词：优先聚焦标的的中文名（自选股里），否则用代码。"""
    symbol = str(ctx.get("focused_symbol") or "").strip()
    watchlist = ctx.get("watchlist")
    if symbol and isinstance(watchlist, list):
        for s in watchlist:
            if isinstance(s, dict) and str(s.get("symbol", "")).upper() == symbol.upper():
                name = str(s.get("name", "") or "").strip()
                if name:
                    return name
    return symbol or None


async def _acquire_fresh_evidence_if_thin(request: GeneralChatRequest) -> int:
    """主动取数（agentic）：挂了证据库且本地强相关证据稀薄时，自动爬一轮补进证据库，
    让 agent「自己去找数据」而非只报"缺"。委托共享 crawl_evidence_if_thin（三路径统一）。"""
    ctx = request.context
    if not isinstance(ctx, dict):
        return 0
    evidence = ctx.get("evidence_sources")
    if not isinstance(evidence, dict) or not evidence.get("retrieve"):
        return 0  # 仅在用户挂了证据库要求检索时才主动取数
    symbol = str(ctx.get("focused_symbol") or "").strip() or None
    keyword = _crawl_keyword_for_context(ctx) or ((request.message or "").strip()[:40] or None)
    return await crawl_evidence_if_thin(symbol, keyword)


def _augment_context_with_retrieval(request: GeneralChatRequest) -> None:
    """检索增强（RAG）：挂载证据库且要求检索时，按本次提问 + 聚焦标的从证据库
    主动拉取相关条目，覆盖前端兜底的 recent_items，让 [n] 内联引用与问题真正相关。

    - 有聚焦标的：symbol + query 词元做相关性检索；为空则回退 symbol-only（最近+高可信）。
    - 无聚焦标的：用提问词元尽力检索。
    - 检索为空时不覆盖（保留前端兜底 recent_items），保证至少有来源可引用。
    list_data_items 已按 credibility_score 优先排序，引用来源天然偏高可信。
    """
    ctx = request.context
    if not isinstance(ctx, dict):
        return
    evidence = ctx.get("evidence_sources")
    if not isinstance(evidence, dict) or not evidence.get("retrieve"):
        return
    symbol = str(ctx.get("focused_symbol") or "").strip() or None
    message = (request.message or "").strip()
    query = message[:80] or None
    try:
        # 取较大候选池（12）供相关性重排，再裁到 top5。
        items: list[Any] = []
        if symbol:
            items = list_data_items(symbol=symbol, query=query, limit=12, sort="time_desc")
            if not items:
                items = list_data_items(symbol=symbol, limit=12, sort="time_desc")
        elif query:
            items = list_data_items(query=query, limit=12, sort="time_desc")
    except Exception:
        return
    if not items:
        return
    # 去重：同题条目（同一文章被多次入库）只保留可信度最高的一条，避免 [2][3][4] 重复引用。
    deduped: list[Any] = []
    seen_titles: set[str] = set()
    for item in items:
        key = (item.title or "").strip()
        if key and key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(item)
    # 相关性重排：按提问 2-gram 词元命中标题+正文的次数排序（credibility 作次序），
    # 让最贴合本次提问的证据排前被引用；提问无重合时退化为原 credibility 序（list 已排好）。
    q_tokens = query_tokens_2gram(message)
    if q_tokens and len(deduped) > 1:
        def _relevance(it: Any) -> int:
            hay = f"{it.title or ''} {it.text_preview or ''}".lower()
            return sum(1 for tok in q_tokens if tok in hay)
        deduped.sort(key=lambda it: (_relevance(it), it.credibility_score), reverse=True)
    evidence["recent_items"] = [
        {
            "title": item.title,
            "symbol": item.symbol,
            "source": item.source_name,
            "url": item.url or "",
            "credibility": item.credibility_score,
            # 用完整正文（截断）而非短 preview，让模型有足够内容做综合+准确引用，而不只是贴标题。
            "preview": (item.text or item.text_preview or "")[:360].strip(),
        }
        for item in deduped[:5]
    ]
    evidence["retrieved"] = True


@app.post("/api/agents/chat", response_model=GeneralChatResponse)
async def general_chat(request: GeneralChatRequest) -> GeneralChatResponse:
    try:
        await _acquire_fresh_evidence_if_thin(request)  # 主动取数：证据不足时自动爬一轮
        _augment_context_with_retrieval(request)
        return attach_data_quality(await llm.general_chat(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/agents/chat/stream")
async def general_chat_stream(request: GeneralChatRequest) -> StreamingResponse:
    """SSE stream of assistant text deltas for the home chat (token-by-token)."""

    async def event_generator() -> AsyncIterator[str]:
        # 主动取数（agentic）：证据不足时先去爬一轮最新资料；期间给前端一个状态提示。
        ctx = request.context if isinstance(request.context, dict) else {}
        ev = ctx.get("evidence_sources")
        if isinstance(ev, dict) and ev.get("retrieve"):
            yield f"data: {json.dumps({'status': '正在检索最新资料…'}, ensure_ascii=False)}\n\n"
            await _acquire_fresh_evidence_if_thin(request)
        # 检索增强：按本次提问主动从证据库拉相关条目（RAG），再抽编号来源。
        _augment_context_with_retrieval(request)
        # 先回传本次可引用的编号来源（前端据此把 [n] 渲染成可点引用 + 来源列表）。
        sources = extract_citable_sources(request.context)
        if sources:
            yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        try:
            async for delta in llm.general_chat_stream(request):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface the error into the client stream
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        else:
            yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _professional_chat_intent(text: str) -> bool:
    return bool(
        re.search(
            r"专业财报|财报库|财报|年报|半年报|季报|研报|电话会|营收|营业收入|净利润|扣非|毛利率|ROE|现金流|资本开支|引用|溯源|评测|回归测试|幻觉",
            text,
            re.I,
        )
    )


async def _maybe_shareholder_change_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_shareholder_change_request(request.message)
    if not skill_request:
        return None

    result = await scan_shareholder_changes(skill_request)
    market_label = {"A": "A股", "HK": "港股", "US": "美股"}.get(result.market, result.market)
    source_label = {
        "A": "巨潮资讯网",
        "HK": "HKEX DI",
        "US": "SEC EDGAR",
    }.get(result.market, result.provider)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="股东增减持扫描",
        content=format_shareholder_change_skill_response(result),
        chips=["shareholder.change.scan", source_label, f"{market_label}披露"],
        suggested_actions=["查看高风险减持", "扩大到30天", "导出公告列表"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": f"识别到全市场 {market_label} 股东增减持扫描意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "shareholder.change.scan",
                "detail": f"调用{source_label}披露检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留公告标题、股票代码、公告日和 PDF 原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.88 if result.records else 0.62,
    )


async def _maybe_cn_earnings_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_cn_earnings_request(request.message)
    if not skill_request:
        return None

    result = await scan_cn_earnings(skill_request)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="A股财报扫描",
        content=format_cn_earnings_skill_response(result),
        chips=["cn.earnings.scan", "巨潮资讯网", "A股财报"],
        suggested_actions=["查看高风险财报", "扩大到90天", "进入A股财报板块"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": "识别到全市场 A 股财报公告扫描意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "cn.earnings.scan",
                "detail": f"调用巨潮公告检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留财报公告标题、核心财务字段、PDF 摘录和原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.86 if result.records else 0.6,
    )


async def _maybe_major_event_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_major_event_request(request.message)
    if not skill_request:
        return None

    result = await scan_major_events(skill_request)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="A股重大事项扫描",
        content=format_major_event_skill_response(result),
        chips=["cn.major_event.scan", "巨潮资讯网", "A股重大事项"],
        suggested_actions=["查看高风险事件", "扩大到90天", "进入事件中心"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": "识别到全市场 A 股重大事项/事件预警意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "cn.major_event.scan",
                "detail": f"调用巨潮公告检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留公告标题、事件标签、PDF 摘录和原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.87 if result.records else 0.62,
    )


def _select_professional_chat_report(request: OrchestratorChatRequest) -> Optional[ProfessionalReportRecord]:
    symbol = request.stock.symbol if request.stock else None
    reports = list_professional_reports(symbol=symbol, limit=1) if symbol else []
    if not reports:
        reports = list_professional_reports(limit=1)
    return reports[0] if reports else None


def _professional_chat_trace(
    request: OrchestratorChatRequest,
    *,
    report: Optional[ProfessionalReportRecord],
    capability: str,
) -> list[dict[str, str]]:
    stock_label = f"{request.stock.name}（{request.stock.symbol}）" if request.stock else "未选择标的"
    report_label = report.title if report else "未命中专业财报库"
    return [
        {
            "phase": "orchestrator",
            "title": "Orchestrator",
            "detail": f"识别为专业财报能力调用，当前标的 {stock_label}。",
            "status": "done",
        },
        {
            "phase": "evidence",
            "title": "Evidence",
            "detail": f"检索专业财报库：{report_label}。",
            "status": "done" if report else "wait",
        },
        {
            "phase": "research",
            "title": "Analyst",
            "detail": f"调用{capability}，使用结构化指标、原文 chunk 和引用约束即时回答。",
            "status": "done" if report else "wait",
        },
        {
            "phase": "risk",
            "title": "RefusalGuard",
            "detail": "证据不足时拒答，不进入自由编造。",
            "status": "done",
        },
    ]


def _format_professional_eval(run: ProfessionalEvalRunResponse) -> str:
    rows = [
        f"评测完成：{run.passed}/{run.total} 通过，Pass Rate {round(run.pass_rate * 100)}%。",
        f"引用覆盖 {round(run.citation_rate * 100)}%，答案命中 {round(run.answer_match_rate * 100)}%，拒答保护 {round(run.refusal_guard_rate * 100)}%。",
    ]
    for case in run.cases[:4]:
        status_label = "通过" if case.passed else "待修正"
        rows.append(f"- {status_label}：{case.question}")
    return "\n".join(rows)


def _format_professional_analysis(result: ProfessionalReportAnalysisResponse) -> str:
    metric_lines = [
        f"- {metric.metric_label}：{metric.raw_value}"
        for metric in result.key_metrics[:6]
    ]
    sections = [
        result.summary,
        "",
        "核心指标：",
        *(metric_lines or ["- 暂未抽取到核心指标"]),
        "",
        "质量/风险：",
        *[f"- {item}" for item in [*result.quality_flags[:3], *result.risks[:3]]],
        "",
        "下一步追问：",
        *[f"- {item}" for item in result.follow_up_questions[:4]],
    ]
    return "\n".join(section for section in sections if section != "")


async def _maybe_professional_research_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    text = request.message.strip()
    if not _professional_chat_intent(text):
        return None

    report = _select_professional_chat_report(request)
    if not report:
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报库",
            content=(
                "专业财报能力已经接入决策台，但当前还没有可用的入库报告。"
                "你可以在这条对话上传财报/研报 PDF，或先到数据源中心入库；入库后我可以直接查指标、给引用、做财报分析和跑评测。"
            ),
            chips=["专业财报库", "待入库", "引用型RAG"],
            suggested_actions=["上传财报", "入库资料", "查看数据源"],
            reasoning_trace=_professional_chat_trace(request, report=None, capability="专业财报库"),
            should_create_task=False,
            handled_inline=True,
            confidence=0.72,
        )

    if re.search(r"评测|回归测试|幻觉|准确率|eval|test", text, re.I):
        eval_run = await run_professional_eval(ProfessionalEvalRunRequest(report_id=report.id))
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报评测",
            content=_format_professional_eval(eval_run),
            chips=["专业财报库", "评测集", f"通过率{round(eval_run.pass_rate * 100)}%"],
            suggested_actions=["查看失败用例", "补充黄金集", "重新入库"],
            reasoning_trace=_professional_chat_trace(request, report=report, capability="专业财报评测"),
            should_create_task=False,
            handled_inline=True,
            confidence=0.84,
        )

    if re.search(r"分析|解读|体检|质量|红旗|风险|追问|总结|报告|agent", text, re.I):
        analysis = await analyze_professional_report(
            report.id,
            ProfessionalReportAnalysisRequest(focus=text, use_cloud_model=True),
        )
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报分析",
            content=_format_professional_analysis(analysis),
            chips=["专业财报库", "财报分析技能", f"{len(analysis.key_metrics)}个指标"],
            suggested_actions=["追问指标", "跑评测", "查看引用"],
            reasoning_trace=_professional_chat_trace(request, report=report, capability="专业财报分析"),
            should_create_task=False,
            handled_inline=True,
            confidence=analysis.confidence,
        )

    rag = await query_professional_rag(
        ProfessionalRagQueryRequest(
            question=text,
            report_id=report.id,
            symbol=report.symbol,
            top_k=6,
            use_cloud_model=True,
        )
    )
    citation_labels = [citation.citation_id for citation in rag.citations[:4]]
    return OrchestratorChatResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        agent="Orchestrator",
        engine=request.engine,
        title="引用型财报问答",
        content=rag.answer,
        chips=["专业财报库", "引用型RAG", *(citation_labels or ["无证据拒答"])],
        suggested_actions=["继续追问", "分析整份财报", "跑评测"],
        reasoning_trace=_professional_chat_trace(request, report=report, capability="引用型财报问答"),
        should_create_task=False,
        handled_inline=True,
        confidence=rag.confidence,
    )


def _is_research_intent(message: str) -> bool:
    msg = message.lower()
    research_keywords = [
        "分析", "调研", "研究", "评估", "诊断",
        "analyze", "research", "evaluate", "assess", "review",
        "怎么样", "怎么看", "如何", "建议", "推荐",
        "财报", "基本面", "估值", "营收", "利润",
        "风险", "仓位", "持仓", "前景", "未来",
        "earning", "financial", "report", "outlook", "risk",
        "季报", "年报", "中报", "业绩",
    ]
    return any(kw in msg for kw in research_keywords)


def _tool_agent_enabled() -> bool:
    """AI 原生 tool-use agent 路径开关。

    已在 MiniMax-M3 上 live 验证 function-calling 可用（模型自主调工具取真实数据），故**默认开**；
    如需回退到既有「预聚合/inline」行为，置 DEEPFOCUS_TOOL_AGENT=0/false/off 显式关闭。
    无论开关，失败都会优雅回退既有路径（红线：不破坏现有体验）。
    """
    return os.getenv("DEEPFOCUS_TOOL_AGENT", "").strip().lower() not in {"0", "false", "no", "off"}


@app.post("/api/agents/cross-module-research", response_model=CrossModuleResearchResponse)
async def cross_module_research(request: CrossModuleResearchRequest) -> CrossModuleResearchResponse:
    if not request.symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    data = await gather_all_for_stock(
        symbol=request.symbol,
        include_macro=request.include_macro,
        include_risk=request.include_risk,
        include_evidence=request.include_evidence,
        include_metrics=request.include_metrics,
        include_supply_chain=request.include_supply_chain,
        include_trade=request.include_trade,
    )
    return CrossModuleResearchResponse(**data)


@app.post("/api/agents/research-loop/stream")
async def research_loop_stream(
    request: Request,
    symbol: str = "",
    question: str = "",
):
    if not symbol or not question:
        raise HTTPException(status_code=400, detail="symbol and question are required")

    async def event_generator() -> AsyncIterator[str]:
        async for event in run_agent_research_loop(llm, symbol, question, request):
            if await request.is_disconnected():
                break
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


def _sse_frame(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/agents/tool-research")
async def tool_research(request: Request, message: str = "", symbol: str = "", name: str = "") -> dict[str, Any]:
    """非流式 AI 原生 tool-use：一次 POST 返回 {ok, answer, tool_trace}。
    与 /stream 同一 agent（iFinD 灰度 + 我们的快讯/研报/复盘工具），但走普通 JSON——经 nginx 比 SSE 稳。
    参数走 query（与 /stream 一致，axios 以 params 传）。"""
    if not message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    _ifind = ifind_enhance_enabled(request)
    hint = f"当前标的：{name}（{symbol}）" if symbol.strip() else ""
    try:
        result = await llm.run_tool_agent(question=message, context_hint=hint, ifind_user=_ifind)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "answer": "", "tool_trace": [], "error": str(exc)[:160]}
    if result and (result.get("answer") or "").strip():
        return {"ok": True, "answer": result["answer"], "tool_trace": result.get("tool_trace", []), "rounds": result.get("rounds", 0)}
    return {"ok": False, "answer": "", "tool_trace": [], "reason": "tool-agent 未返回结果"}


@app.post("/api/agents/tool-research/stream")
async def tool_research_stream(request: Request, message: str = "", symbol: str = "", name: str = ""):
    """流式 AI 原生 tool-use：边调工具边把进度（tool_start / tool_result）实时推给前端，最后 final/error/fallback。

    打磨「研究类问题等 15-30 秒」的体验——让用户看到模型正在调哪些工具，而不是干等一个转圈。
    tool-agent 无答案（未启用 / 不支持）→ 发 fallback，前端回退到非流式 orchestrator-chat。
    """
    if not message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    _ifind = ifind_enhance_enabled(request)  # 仅白名单(lx199710) A股走 iFinD；匿名/失效 token → False(不抛)

    async def event_generator() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event_type: str, payload: dict) -> None:
            await queue.put(_sse_frame(event_type, payload))

        hint = f"当前标的：{name}（{symbol}）" if symbol.strip() else ""

        async def run() -> None:
            try:
                result = await llm.run_tool_agent(question=message, context_hint=hint, emit=emit, ifind_user=_ifind)
                if result and (result.get("answer") or "").strip():
                    await queue.put(_sse_frame("final", {
                        "answer": result["answer"],
                        "tool_trace": result.get("tool_trace", []),
                        "rounds": result.get("rounds", 0),
                    }))
                else:
                    await queue.put(_sse_frame("fallback", {"reason": "tool-agent 未返回结果"}))
            except Exception as exc:
                await queue.put(_sse_frame("error", {"message": str(exc)[:200]}))
            finally:
                await queue.put(None)  # 哨兵：通知生成器结束

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if await request.is_disconnected():
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ============================ 深度研判（多智能体辩论，灰度 lx199710）============================
# TradingAgents 式范式跑在现有栈上：取证→多空立论→空头反驳+风控→投委会裁判。
# 纯轮询（POST 起任务 + GET 拉进度），绝不用 SSE（生产 nginx HTTP/2 下 POST+SSE→444）。
# 全功能仅灰度（_require_ifind_user 硬门 403），iFinD 衍生结论只活在内存任务表（见 deep_research.py）。
from . import deep_research as dr  # noqa: E402


@app.post("/api/agents/deep-research")
async def deep_research_start(request: Request, symbol: str = "", name: str = "", market: str = "CN", force: int = 0) -> dict[str, Any]:
    """发起一次深度研判，返回 {task_id, status}。前端随后轮询 GET 端点。force=1 强制重跑（绕过复用）。"""
    claims = _require_ifind_user(request)  # 登录+白名单否则 403（整功能仅灰度）
    owner = str(claims.get("username") or "").strip().lower()
    if not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    ifind_used = ifind_enhance_enabled(request)  # 白名单恒 True，但仍走统一判定不裸传

    # 限流（1.8G 机器防 OOM）：同一用户已有研判在跑 → 复用；全局 in-flight 上限 → 429。
    existing = await dr.owner_running(owner)
    if existing:
        return {"task_id": existing, "status": "running", "reused": True}
    # 省 token：非强制时，同用户对同股 10min 内已完成研判直接复用（0 次 LLM 调用）。
    if not force:
        cached = await dr.recent_done(owner, symbol, market)
        if cached:
            return {"task_id": cached, "status": "done", "reused": True}
    if await dr.in_flight_count() >= dr.GLOBAL_INFLIGHT_CAP:
        raise HTTPException(status_code=429, detail="深度研判排队中，请稍候再试")

    task = await dr.create_task(owner, ifind_used, symbol, name, market)
    asyncio.create_task(dr.run_deep_research(task.task_id, symbol, name, task.market, ifind_user=ifind_used))
    return {"task_id": task.task_id, "status": "pending"}


@app.get("/api/agents/deep-research/{task_id}")
async def deep_research_poll(request: Request, task_id: str) -> dict[str, Any]:
    """轮询深度研判进度/结果。⭐此端点也必须门控（最易漏）+ owner 校验（非属主→404 不泄漏存在性）。"""
    claims = _require_ifind_user(request)
    owner = str(claims.get("username") or "").strip().lower()
    task = await dr.get_task(task_id)
    if not task or task.owner != owner:
        raise HTTPException(status_code=404, detail="研判任务不存在")
    return dr.to_public(task)


async def _route_orchestrator_chat(
    request: OrchestratorChatRequest,
    _ifind: bool,
    tool_timeout: float = 30.0,
    force_research: bool = False,
    skip_professional: bool = False,
) -> OrchestratorChatResponse:
    """orchestrator-chat 路由内核（HTTP 端点与微信「扫码即问」共用，避免重复造轮子）：
    依次试技能(股东/财报/重大事件/专业研报) → 研究意图则跑 tool-agent → 有 ticker 则跨模块注入 → 兜底。
    - tool_timeout：tool-agent 每轮 LLM 超时；微信个股问答多轮取数需更长(传 60)，HTTP 端点用默认 30。
    - force_research：微信场景几乎全是投研提问，强制走研究路径(确保调工具取真数)，避免「值得关注吗」这类
      不含意图关键词的问句漏判 _is_research_intent → 落到不取数的朴素 orchestrator。"""
    shareholder_change_reply = await _maybe_shareholder_change_skill_chat(request)
    if shareholder_change_reply:
        return attach_data_quality(shareholder_change_reply)
    cn_earnings_reply = await _maybe_cn_earnings_skill_chat(request)
    if cn_earnings_reply:
        return attach_data_quality(cn_earnings_reply)
    major_event_reply = await _maybe_major_event_skill_chat(request)
    if major_event_reply:
        return attach_data_quality(major_event_reply)
    # 专业研报技能=「上传 PDF/入库报告」的 IC 工作台,微信用户无法上传→对微信是死路;
    # skip_professional=True 时跳过它,让"总结最近研报"落到 get_recent_research(读网站研报wire/缓存)。
    if not skip_professional:
        professional_reply = await _maybe_professional_research_chat(request)
        if professional_reply:
            return attach_data_quality(professional_reply)

    stock_symbol = (request.stock.symbol or "").strip() if request.stock else ""
    research_intent = force_research or _is_research_intent(request.message)

    # ① AI 原生 tool-use agent：研究意图即触发（stock 可选——模型自主选工具、自行取数）。
    #    个股+研究意图的消息已在前端被路由去研究 Loop，故到这里的研究问题多为「无显式 ticker」，
    #    正好交给 tool-agent 让模型自己决定调哪些工具。返回 None（未启用/工具不支持/失败）则落到既有路径。
    if research_intent and _tool_agent_enabled():
        try:
            hint = (
                f"当前标的：{(request.stock.name or '') if request.stock else ''}（{stock_symbol}）"
                if stock_symbol else ""
            )
            agent_result = await llm.run_tool_agent(
                question=request.message, context_hint=hint, ifind_user=_ifind, timeout_seconds=tool_timeout
            )
            if agent_result:
                mapped = tool_agent_to_orchestrator_response(
                    agent_result, request, llm.provider_name, llm.model
                )
                if mapped:
                    return attach_data_quality(mapped)
        except Exception:
            pass

    # ② 有 stock 的研究意图：服务端预聚合跨模块数据 → 注入 → 合成（既有路径）。
    if stock_symbol and research_intent:
        try:
            aggregated = await gather_all_for_stock(
                stock_symbol,
                include_macro=request.include_macro,
                include_risk=request.include_risk,
                include_evidence=request.include_evidence,
                include_metrics=request.include_metrics,
                include_supply_chain=request.include_supply_chain,
                include_trade=request.include_trade,
            )
            injection = build_injection_block(aggregated)
            return attach_data_quality(await llm.orchestrator_chat_with_context(request, injection))
        except Exception:
            pass

    return attach_data_quality(await llm.orchestrator_chat(request))


def make_weixin_orchestrator_agent_fn():
    """微信「扫码即问」改用 orchestrator 路由(复用技能 + 智能路由)，而非裸 run_tool_agent。
    单轮无历史；强制研究路径 + tool-agent 超时放宽(env DEEPFOCUS_WEIXIN_QA_TIMEOUT，默认60)；取 content 作答。
    cache/每日配额/合规中性化仍由 weixin_channel._handle_batch 包在外层，此处只负责产出答案文本。"""
    _wx_timeout = float(os.getenv("DEEPFOCUS_WEIXIN_QA_TIMEOUT", "60") or 60)

    async def _agent(question: str, hint: str):
        message = f"{hint}\n\n{question}" if hint else question
        try:
            resp = await _route_orchestrator_chat(
                OrchestratorChatRequest(message=message),
                _ifind=False,
                tool_timeout=_wx_timeout,
                force_research=True,
                skip_professional=True,  # 微信无法上传PDF→跳过IC工作台技能,研报问落到 get_recent_research 读网站缓存
            )
        except Exception:
            return None
        return (getattr(resp, "content", "") or "").strip() or None

    return _agent


@app.post("/api/agents/orchestrator-chat", response_model=OrchestratorChatResponse)
async def orchestrator_chat(request: OrchestratorChatRequest, http_request: Request) -> OrchestratorChatResponse:
    # http_request 由 FastAPI 注入（不改 body schema）——仅用于 iFinD 灰度判定。
    _ifind = ifind_enhance_enabled(http_request)
    try:
        return await _route_orchestrator_chat(request, _ifind)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/risk/greeks", response_model=GreeksResponse)
async def risk_greeks(request: GreeksRequest) -> GreeksResponse:
    result = calculate_greeks(
        underlying_price=request.underlying_price,
        strike=request.strike,
        days_to_expiry=request.days_to_expiry,
        risk_free_rate=request.risk_free_rate,
        implied_vol=request.implied_vol,
        option_type=request.option_type,
    )
    return GreeksResponse(**result)


@app.get("/api/risk/positions", response_model=PositionListResponse)
async def risk_positions(status: Optional[str] = None) -> PositionListResponse:
    positions = list_positions(status=status if status else None)
    enriched = []
    for pos in positions:
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    return PositionListResponse(positions=[PositionRecord(**p) for p in enriched])


@app.post("/api/risk/positions", response_model=PositionRecord)
async def risk_create_position(request: PositionCreateRequest) -> PositionRecord:
    pos = create_position(
        symbol=request.symbol,
        name=request.name,
        market=request.market,
        asset_class=request.asset_class,
        direction=request.direction,
        entry_price=request.entry_price,
        quantity=request.quantity,
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
        position_size_pct=request.position_size_pct,
        sector=request.sector,
        strategy=request.strategy,
        notes=request.notes,
        tags=request.tags,
        greeks=request.greeks,
    )
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.get("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_get_position(position_id: str) -> PositionRecord:
    pos = get_position(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.put("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_update_position(position_id: str, request: PositionUpdateRequest) -> PositionRecord:
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    pos = update_position(position_id, **updates)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.delete("/api/risk/positions/{position_id}")
async def risk_delete_position(position_id: str) -> dict:
    if not delete_position(position_id):
        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "deleted", "id": position_id}


@app.post("/api/risk/positions/{position_id}/close", response_model=PositionRecord)
async def risk_close_position(position_id: str, request: PositionCloseRequest) -> PositionRecord:
    try:
        pos = close_position(position_id, request.exit_price, request.exit_reason)
    except PositionAlreadyClosedError:
        raise HTTPException(status_code=409, detail="该持仓已平仓，请勿重复平仓。")
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.post("/api/risk/positions/refresh")
async def risk_refresh_prices() -> dict:
    updated = refresh_position_prices()
    return {"status": "ok", "updated_count": len(updated), "positions": updated}


@app.get("/api/risk/summary", response_model=RiskSummaryResponse)
async def risk_summary() -> RiskSummaryResponse:
    data = get_risk_summary()
    from .risk_management import calculate_position_risk
    enriched = []
    for pos in data.get("open_positions", []):
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    data["open_positions"] = enriched
    return RiskSummaryResponse(**data)


@app.get("/api/risk/limits")
async def risk_limits() -> list[RiskLimitRecord]:
    limits = get_risk_limits()
    return [RiskLimitRecord(**lim) for lim in limits]


@app.put("/api/risk/limits/{key}", response_model=RiskLimitRecord)
async def risk_update_limit(key: str, request: RiskLimitUpdateRequest) -> RiskLimitRecord:
    lim = update_risk_limit(key, request.value, request.enabled)
    if not lim:
        raise HTTPException(status_code=404, detail="Risk limit not found")
    return RiskLimitRecord(**lim)


@app.get("/api/risk/pnl", response_model=PnlSummaryResponse)
async def risk_pnl_summary() -> PnlSummaryResponse:
    return PnlSummaryResponse(**get_pnl_summary())


@app.get("/api/risk/pnl/records")
async def risk_pnl_records(position_id: Optional[str] = None, limit: int = 100) -> list[PnlRecord]:
    records = list_pnl_records(position_id=position_id, limit=limit)
    return [PnlRecord(**r) for r in records]


@app.post("/api/backtest/{backtest_id}/run")
async def backtest_run(backtest_id: str, request: Request) -> StreamingResponse:
    bt = get_backtest(backtest_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if bt.get("status") == "running":
        raise HTTPException(status_code=409, detail="Backtest is already running")

    async def event_gen():
        async for event in run_backtest(backtest_id, request):
            if await request.is_disconnected():
                break
            yield event

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive",
            "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/backtest/aggregate")
async def backtest_aggregate_for_research(symbol: str = "") -> dict:
    if not symbol:
        return {"backtests": [], "symbol": "", "total": 0}
    return await list_backtest_results(symbol)


@app.get("/api/backtest", response_model=BacktestListResponse)
async def backtest_list(limit: int = 50) -> BacktestListResponse:
    backtests = list_backtests(limit=limit)
    return BacktestListResponse(backtests=[BacktestRecord(**bt) for bt in backtests])


@app.post("/api/backtest", response_model=BacktestRecord)
async def backtest_create(request: BacktestCreateRequest) -> BacktestRecord:
    bt = create_backtest(
        name=request.name,
        market=request.market,
        strategy_type=request.strategy_type,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_capital=request.initial_capital,
        benchmark=request.benchmark,
        parameters=request.parameters,
    )
    return BacktestRecord(**bt)


@app.get("/api/backtest/{backtest_id}", response_model=BacktestRecord)
async def backtest_get(backtest_id: str) -> BacktestRecord:
    bt = get_backtest(backtest_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestRecord(**bt)


@app.delete("/api/backtest/{backtest_id}")
async def backtest_delete(backtest_id: str) -> dict:
    if not delete_backtest(backtest_id):
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {"status": "deleted", "id": backtest_id}


@app.post("/api/backtest/metrics", response_model=BacktestMetricsResponse)
async def backtest_metrics(request: BacktestMetricsRequest) -> BacktestMetricsResponse:
    metrics = calculate_backtest_metrics(
        equity_curve=request.equity_curve,
        benchmark_curve=request.benchmark_curve,
        initial_capital=request.initial_capital,
    )
    return BacktestMetricsResponse(**metrics)


@app.get("/api/market-dashboard", response_model=MarketDashboardResponse)
async def market_dashboard() -> MarketDashboardResponse:
    data = await fetch_market_dashboard()
    return MarketDashboardResponse(**data)


@app.get("/api/market-dashboard/ashare", response_model=MarketDashboardResponse)
async def ashare_dashboard() -> MarketDashboardResponse:
    data = await fetch_ashare_dashboard()
    return MarketDashboardResponse(**data)


@app.post("/api/market-dashboard/analyze", response_model=DashboardAnalysisResponse)
async def market_dashboard_analyze() -> DashboardAnalysisResponse:
    dashboard = await fetch_market_dashboard()
    indicators_data = json.dumps(
        [
            {
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "signal": ind["signal"],
                "status": ind["status"],
            }
            for cat in dashboard.get("categories", [])
            for ind in cat.get("indicators", [])
        ],
        ensure_ascii=False,
    )
    result = await llm.analyze_market_dashboard(
        title=f"整体信号：{dashboard['overall_signal']} (评分{dashboard['overall_score']})",
        indicators_json=indicators_data,
        market_type="global",
    )
    return DashboardAnalysisResponse(**result)


@app.post("/api/market-dashboard/ashare/analyze", response_model=DashboardAnalysisResponse)
async def ashare_dashboard_analyze() -> DashboardAnalysisResponse:
    dashboard = await fetch_ashare_dashboard()
    indicators_data = json.dumps(
        [
            {
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "signal": ind["signal"],
                "status": ind["status"],
            }
            for cat in dashboard.get("categories", [])
            for ind in cat.get("indicators", [])
        ],
        ensure_ascii=False,
    )
    result = await llm.analyze_market_dashboard(
        title=f"A股整体信号：{dashboard['overall_signal']} (评分{dashboard['overall_score']})",
        indicators_json=indicators_data,
        market_type="ashare",
    )
    return DashboardAnalysisResponse(**result)
