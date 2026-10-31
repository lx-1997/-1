// Executes the composed shipped workspace with controlled transport and storage.
const vm=require('node:vm'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const html=execFileSync('python3',['-c','from deepfocus_api.quant_research_ui import HTML;print(HTML)'],{env:{...process.env,PYTHONPATH:'backend'}}).toString();
const code=html.split('<script>')[1].split('</script>')[0],tick=()=>new Promise(setImmediate);
const storage=()=>{const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)}};
const response=value=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>value});
const job=(id,stage)=>({id,status:'failed',stage,request:{name:stage,symbols:['000333']},events:[]});
async function harness(){
 const nodes=new Map(),events=new Map(),auth=storage(),session=storage();auth.setItem('auth_token','owner-a');
 function node(id){if(!nodes.has(id)){const classes=new Set();nodes.set(id,{innerHTML:'',textContent:'',value:'',disabled:false,isConnected:true,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,f){if(f===undefined)f=!classes.has(x);f?classes.add(x):classes.delete(x);return f}},setAttribute(){},addEventListener:(t,f)=>events.set(id+':'+t,f),insertAdjacentHTML(_where,s){this.innerHTML+=s},scrollIntoView(){}})}return nodes.get(id)}
 // Match the DOM effect of assigning textContent: old markup is removed.
 const originalNode=node;
 function domNode(id){const value=originalNode(id);if(!value._textProperty){value._textProperty=true;Object.defineProperty(value,'textContent',{get(){return this._plain||''},set(v){this._plain=String(v);this.innerHTML=String(v)}})}return value}
 let handler=async()=>response({items:[]}),requests=0;
 const context={console,Date,JSON,Number,String,Object,Set,Promise,AbortController,crypto:{randomUUID:()=> 'fixture-request'},localStorage:auth,sessionStorage:session,
  document:{body:domNode('body'),hidden:false,getElementById:domNode,querySelector:()=>null,addEventListener:(t,f)=>events.set('document:'+t,f)},
  window:{innerWidth:1200,matchMedia:()=>({matches:false}),addEventListener:(t,f)=>events.set('window:'+t,f)},
  fetch:async(...args)=>{requests++;return handler(...args)},setTimeout:()=>1,clearTimeout(){}};
 vm.createContext(context);vm.runInContext(code,context);await tick();
 return {context,node:domNode,auth,session,events,setHandler:h=>handler=h,requests:()=>requests,read:s=>vm.runInContext(s,context)};
}
const failures=[];
async function test(name,run){try{await run();console.log('PASS:',name)}catch(e){failures.push(name);console.error('FAIL:',name, e.message)}}
(async()=>{
 await test('discard JSON body resolved after account changes',async()=>{
  const h=await harness();let resolve;h.setHandler(async()=>({...response({}),json:()=>new Promise(r=>resolve=r)}));
  const pending=h.context.select('delayed-a').catch(e=>e);await tick();h.auth.setItem('auth_token','owner-b');resolve(job('delayed-a','PRIVATE_ACCOUNT_A'));
  const error=await pending;assert.match(error?.message||'',/账号已切换/);assert.doesNotMatch(h.node('result').innerHTML,/PRIVATE_ACCOUNT_A/);
 });
 await test('same-owner latest selection wins even when earlier response arrives last',async()=>{
  const h=await harness();let release;h.setHandler(async url=>url.endsWith('/A')?new Promise(r=>release=r):response(job('B','LATEST_B')));
  const a=h.context.select('A');await tick();await h.context.select('B');release(response(job('A','STALE_A')));await a;
  assert.match(h.node('result').innerHTML,/LATEST_B/);assert.doesNotMatch(h.node('result').innerHTML,/STALE_A/);
 });
 await test('reset invalidates a response body even without a token change',async()=>{
  const h=await harness();let resolve;h.setHandler(async()=>({...response({}),json:()=>new Promise(r=>resolve=r)}));
  const pending=h.context.select('old-view').catch(e=>e);await tick();h.context.reset();resolve(job('old-view','STALE_AFTER_RESET'));
  const error=await pending;assert.match(error?.message||'',/账号已切换/);assert.doesNotMatch(h.node('result').innerHTML,/STALE_AFTER_RESET/);
 });
 await test('pagehide clears rendered private data but preserves accepted submission receipts',async()=>{
  const h=await harness();h.node('result').innerHTML='PRIVATE_RESULT';h.node('papers').innerHTML='PRIVATE_POSITIONS';h.node('history').innerHTML='PRIVATE_HISTORY';
  for(const key of ['quant_research_pending','quant_event_pending','quant_calendar_pending'])h.session.setItem(key,JSON.stringify({job_id:'accepted'}));
  h.events.get('window:pagehide')();
  for(const id of ['result','papers','history'])assert.doesNotMatch(h.node(id).innerHTML,/PRIVATE_/);
  for(const key of ['quant_research_pending','quant_event_pending','quant_calendar_pending'])assert.equal(JSON.parse(h.session.getItem(key)).job_id,'accepted');
 });
 await test('persisted pageshow reloads with current account instead of restoring old results',async()=>{
  const h=await harness();h.events.get('window:pagehide')();h.auth.setItem('auth_token','owner-b');const before=h.requests();
  assert.ok(h.events.has('window:pageshow'));h.events.get('window:pageshow')({persisted:true});await tick();await tick();
  assert.ok(h.requests()>before);assert.equal(h.node('result').innerHTML,'');assert.doesNotMatch(h.node('history').innerHTML,/PRIVATE_/);
 });
 await test('load checks owner before committing registry AND proof lists',async()=>{
  const h=await harness();h.read("selected={id:'existing'}");let calls=0;
  h.context.api=async path=>{calls++;if(calls===4)h.auth.setItem('auth_token','owner-b');return {items:path==='/paper'?[]:path==='/experiments'?[{id:'a',request:{name:'PRIVATE_OLD_REGISTRY'},status:'failed'}]:[{id:'a',label:'PRIVATE_OLD_PROOF',years:[2024],symbols:{'000333':1}}]}};
  await h.context.load();assert.doesNotMatch(h.node('history').innerHTML,/PRIVATE_OLD_REGISTRY/);
  assert.ok(h.read("evidenceItems.every(x=>!x.label.includes('PRIVATE_')) && calendarItems.every(x=>!x.label.includes('PRIVATE_'))"));
 });
 assert.equal(failures.length,0,failures.join('; '));console.log('PASS: all workspace response, selection, reset and history isolation controls');
})().catch(e=>{console.error(e.message);process.exitCode=1});
