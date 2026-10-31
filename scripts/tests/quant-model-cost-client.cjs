// Shipped read-only client under a synthetic transport; browser QA is separate.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_model_cost_ui import HTML;print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const nodes=new Map(),listeners=new Map();
function node(id){if(!nodes.has(id)){const c=new Set();nodes.set(id,{innerHTML:'',textContent:'',disabled:false,scrollIntoView(){},focus(){},classList:{add:x=>c.add(x),remove:x=>c.delete(x),contains:x=>c.has(x),toggle(x,f){if(f===undefined)f=!c.has(x);f?c.add(x):c.delete(x);return f}},addEventListener:(t,fn)=>listeners.set(id+':'+t,fn)})}return nodes.get(id)}
const storage=new Map([['auth_token','owner-a']]),auth={getItem:k=>storage.get(k)||'',setItem:(k,v)=>storage.set(k,v)};
const response=(v,status=200)=>({ok:status<400,status,headers:{get:()=> 'application/json'},json:async()=>v});let handler=async()=>response({items:[],next_cursor:null}),writes=0;
const context={console,Date,JSON,Number,String,Map,Set,Promise,AbortController,localStorage:auth,fetch:async(u,o)=>{if(o.method&&o.method!=='GET')writes++;return handler(u,o)},document:{getElementById:node,addEventListener:(t,fn)=>listeners.set('doc:'+t,fn)},setTimeout:()=>1,clearTimeout:()=>{}};
context.window={innerWidth:390,addEventListener:(t,fn)=>listeners.set('window:'+t,fn)};vm.createContext(context);vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const e={experiment:'original',original_oos_return_pct:-6.573664,original_gate:{passed:false,reasons:['<img src=x onerror=bad>']},exact_conservation_verified:true,fills:58,days:535,legacy_strict_status:'rejected',legacy_difference_count:1,differences:[{date:'2025-01-10',symbol:'000333',legacy_cents:21989,exact_model_rounded_cents:21988,legacy_minus_exact_cents:1}],rounding_residual:['7','100'],restore:{full_state_and_outputs_equal:true}};
const record={id:'record',label:'<img src=x onerror=bad> archive',recorded_at:'2026-10-31T00:00:00Z',source_version_current:true,fingerprint:'fp',experiments:[e]};
(async()=>{
 await new Promise(setImmediate);context.render(record);assert.match(node('detail').innerHTML,/&lt;img/);assert.doesNotMatch(node('detail').innerHTML,/<img/);assert.match(node('detail').innerHTML,/-6\.57%/);assert.match(node('detail').innerHTML,/旧账务严格对账/);assert.match(node('detail').innerHTML,/219\.89/);assert.match(node('detail').innerHTML,/219\.88/);assert.doesNotMatch(node('detail').innerHTML,/data-promote|data-order/);
 context.render({...record,source_version_current:false,experiments:[{...e,original_oos_return_pct:98765}]});assert.match(node('detail').innerHTML,/旧版本档案/);assert.doesNotMatch(node('detail').innerHTML,/98765|219\.89/);
 let resolveA;handler=async u=>u.endsWith('/A')?new Promise(r=>resolveA=r):response({...record,id:'B',label:'LATEST B'});
 const a=context.selectArchive('A').catch(e=>e);await new Promise(setImmediate);await context.selectArchive('B');resolveA(response({...record,id:'A',label:'STALE A'}));assert.ok((await a).stale);assert.match(node('detail').innerHTML,/LATEST B/);assert.doesNotMatch(node('detail').innerHTML,/STALE A/);
 let resolveJSON;handler=async()=>({...response({}),json:()=>new Promise(r=>resolveJSON=r)});
 const pending=context.api('/model-cost-archives').catch(e=>e);await new Promise(setImmediate);auth.setItem('auth_token','owner-b');resolveJSON({items:[record]});assert.ok((await pending).stale);assert.equal(node('detail').textContent,'');assert.equal(node('records').textContent,'');assert.ok(node('content').classList.contains('hidden'));
 context.render(record);listeners.get('window:pagehide')();assert.equal(node('detail').textContent,'');assert.ok(node('content').classList.contains('hidden'));
 handler=async()=>({ok:false,status:503,headers:{get:()=> 'text/html'},json:async()=>{throw Error('must not parse HTML')}});await assert.rejects(context.api('/model-cost-archives'),/档案服务暂不可用/);
 assert.equal(writes,0);console.log('PASS: independent gate/cost/legacy labels, escaped values, stale metrics hidden, late selection/account body race discarded, pagehide private data cleared, HTML errors localized, GET only');
})().catch(e=>{console.error(e);process.exitCode=1});
