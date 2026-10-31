// Run shipped read-only archive client in a fake transport; no account or real HTTP.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_share_ui import HTML; print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const nodes=new Map(),listeners=new Map();
function node(id){if(!nodes.has(id)){const c=new Set();nodes.set(id,{innerHTML:'',textContent:'',disabled:false,scrollIntoView(){this.scrolled=true},classList:{add:x=>c.add(x),remove:x=>c.delete(x),contains:x=>c.has(x),toggle(x,f){if(f===undefined)f=!c.has(x);f?c.add(x):c.delete(x);return f}},addEventListener(t,fn){listeners.set(id+':'+t,fn)}})}return nodes.get(id)}
const storage=new Map([['auth_token','owner-a']]);const auth={getItem:k=>storage.get(k)||'',setItem:(k,v)=>storage.set(k,v)};
const response=(d,status=200)=>({ok:status<400,status,headers:{get:()=> 'application/json'},json:async()=>d});
let handler=async()=>response({items:[],next_cursor:null}),posts=0;
const context={console,Date,JSON,Number,String,Map,Set,Promise,AbortController,localStorage:auth,fetch:async(u,opt)=>{if(opt.method&&opt.method!=='GET')posts++;return handler(u,opt)},document:{getElementById:node,addEventListener:(t,fn)=>listeners.set('doc:'+t,fn)},setTimeout:()=>1,clearTimeout:()=>{}};
context.window={innerWidth:390,addEventListener:(t,fn)=>listeners.set('window:'+t,fn)};vm.createContext(context);vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const b={id:'bundle',label:'<img src=x onerror=bad>',symbol:'300750',position_role:'counterfactual_model',record_date:'2023-04-25',source_version_current:true,recorded_at:'2026-10-31T00:00:00Z',component_visibility:{action:'2026-11-01T00:00:00+08:00',position_snapshot:'2026-11-01T00:00:00+08:00'},action:{symbol:'300750',kind:'capital_reserve',record_date:'2023-04-25',ex_date:'2023-04-26',share_credit_date:'2023-04-26',unrestricted_trade_date:'2023-04-26',new_shares_per_old_share:'0.8'},declared_old_shares:100,source_count:1,fingerprint:'test-fingerprint'};
const r={id:'review',bundle_id:'bundle',label:b.label,symbol:b.symbol,position_role:b.position_role,source_version_current:true,verification_level:'evidence_and_quantity_checked',mode:'historical_reconstruction',as_of:'2023-04-26T12:00:00+08:00',recorded_at:b.recorded_at,status:'awaiting_allotment',quantity:{registered_old_shares:1,exact_new_shares:{unrestricted:'0.8',restricted:'0'},credited:null,available_new_unrestricted_shares:0},fingerprint:'review-fingerprint'};
(async()=>{
 await new Promise(setImmediate);
 context.renderBundle(b);assert.match(node('detail').innerHTML,/&lt;img/);assert.doesNotMatch(node('detail').innerHTML,/<img/);assert.match(node('detail').innerHTML,/假设持仓/);
 context.renderReview(r);assert.match(node('detail').innerHTML,/缺少获配确认/);assert.match(node('detail').innerHTML,/尚未确认/);assert.match(node('detail').innerHTML,/0\.8/);assert.doesNotMatch(node('detail').innerHTML,/data-promote|data-order/);
 context.renderReview({...r,source_version_current:false,quantity:{...r.quantity,registered_old_shares:987654}});assert.match(node('detail').innerHTML,/旧版本复核记录/);assert.doesNotMatch(node('detail').innerHTML,/987654/);
 // An older selection resolves after a newer one: only the current one renders.
 let resolveA;handler=async url=>url.includes('/share-evidence/A')?new Promise(resolve=>resolveA=resolve):url.includes('/share-evidence/B')?response({...b,id:'B',label:'LATEST B'}):response({items:[],next_cursor:null});
 const a=context.selectBundle('A').catch(e=>e);await new Promise(setImmediate);await context.selectBundle('B');resolveA(response({...b,id:'A',label:'STALE A'}));const stale=await a;assert.ok(stale.stale);assert.match(node('detail').innerHTML,/LATEST B/);assert.doesNotMatch(node('detail').innerHTML,/STALE A/);
 // Account changes while response.json() is pending, after HTTP headers arrived.
 let resolveJSON;handler=async()=>({...response({}),json:()=>new Promise(resolve=>resolveJSON=resolve)});
 const pending=context.api('/share-evidence').catch(e=>e);await new Promise(setImmediate);auth.setItem('auth_token','owner-b');resolveJSON({items:[b]});assert.ok((await pending).stale);assert.equal(node('detail').textContent,'');assert.equal(node('bundles').textContent,'');assert.equal(node('content').classList.contains('hidden'),true);
 handler=async()=>({ok:false,status:503,headers:{get:()=> 'text/html'},json:async()=>{throw Error('must not parse HTML')}});
 await assert.rejects(context.api('/share-evidence'),/档案服务暂不可用/);assert.equal(posts,0);
 console.log('PASS: escaped labels, pending != zero credit, stale version hides quantity, latest selection wins, account switch during body parse clears data, HTML error localized, no writes');
})().catch(e=>{console.error(e);process.exitCode=1});
