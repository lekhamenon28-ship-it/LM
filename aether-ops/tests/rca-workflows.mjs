import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import worker from '../dist/server/index.js';
import {fetchWithThrottleRetry} from '../worker/provider-retry.mjs';

const sqlite=new DatabaseSync(':memory:');
const DB={prepare(sql){let args=[];return {bind(...v){args=v;return this},async run(){return {meta:{changes:Number(sqlite.prepare(sql).run(...args).changes)}}},async first(){return sqlite.prepare(sql).get(...args)||null},async all(){return {results:sqlite.prepare(sql).all(...args)}}}}};
const env={DB,BOOTSTRAP_ADMIN_USERNAME:'admin',BOOTSTRAP_ADMIN_PASSWORD:'rca-admin-password',CREDENTIAL_ENCRYPTION_KEY:'c'.repeat(64),ASSETS:{fetch:async()=>new Response('')}};
const origin='https://app.test';
const call=(path,method='GET',data,cookie)=>worker.fetch(new Request(origin+path,{method,headers:{Origin:origin,...(data?{'Content-Type':'application/json'}:{}),...(cookie?{Cookie:cookie}:{})},...(data?{body:JSON.stringify(data)}:{})}),env);
async function login(username,password){const r=await call('/api/auth/login','POST',{username,password});assert.equal(r.status,200);return r.headers.get('Set-Cookie').split(';')[0]}
const admin=await login('admin','rca-admin-password');
const api=(path,method='POST',data={},cookie=admin)=>call('/api/workspace'+path,method,data,cookie);
const snapshot=async()=>(await call('/api/workspace','GET',null,admin)).json();
async function create(kind,data){const r=await api('/'+kind,'POST',data);assert.equal(r.status,201,JSON.stringify(await r.clone().json()));return (await r.json()).id}
await call('/api/users','POST',{username:'operator',password:'operator-password'},admin);const operator=await login('operator','operator-password');
const user=(await snapshot()).users.find(x=>x.username==='operator');
const source=await create('documents',{title:'Database pool recovery',tags:'database,pool',content:'Confirm pool wait times before proposing capacity changes.',status:'approved'});
await create('documents',{title:'Database unsafe draft',tags:'database,pool',content:'An unverified suggestion',status:'draft'});
assert.equal((await api('/documents','POST',{title:'Bad approval',tags:'',content:'Not reviewed',status:'approved'},operator)).status,403);
await api('/personas','POST',{userId:user.id,persona:'builder'});
assert.equal((await api('/documents','POST',{title:'Bad approval',tags:'',content:'Not reviewed',status:'approved'},operator)).status,403);
let r=await api('/knowledge/search','POST',{query:'database pool',approvedOnly:true},operator);assert.equal(r.status,200);assert.deepEqual((await r.json()).sources.map(s=>s.id),[source]);
assert.deepEqual((await (await api('/knowledge/search','POST',{query:'unmatchedfoobar'})).json()).sources,[]);
await api('/credentials','POST',{name:'TOOL_DT',secret:'dynatrace-fixture-secret'});
await api('/credentials','POST',{name:'MODEL_RCA',secret:'model-fixture-secret'});
const entities=await create('tools',{name:'Dynatrace entities',description:'Existing connection',method:'GET',authType:'api-token',endpoint:'https://tenant.dynatrace.com/api/v2/entities',secretRef:'TOOL_DT',inputSchema:{type:'object',additionalProperties:true}});
assert.equal((await api('/rca/problems-tool','POST',{toolId:entities},operator)).status,403);
r=await api('/rca/problems-tool','POST',{toolId:entities});assert.equal(r.status,201);const problemsTool=(await r.json()).id;
assert.equal((await (await api('/rca/problems-tool','POST',{toolId:entities})).json()).id,problemsTool);
const model=await create('models',{name:'Fixture model',provider:'Fixture',model:'configured-fixture-model',endpoint:'https://models.acme.com/v1/chat/completions',secretRef:'MODEL_RCA',context:8192,purpose:'RCA'});
const flow=await create('rca-workflows',{name:'Davis and SOP RCA',toolId:problemsTool,modelId:model,from:'now-30d'});
assert.equal((await api('/tools/'+problemsTool,'DELETE')).status,409);
assert.equal((await api('/models/'+model,'DELETE')).status,409);
assert.equal((await api('/rca/workflows/'+flow+'/run','POST',{},null)).status,401);
const actual=globalThis.fetch;let mode='normal',pageCalls=[],modelCalls=[],lastMessages=[];
const problem=n=>({problemId:'P-'+n,displayId:'P-'+n,title:'Database pool exhausted',status:'OPEN',rootCauseEntity:{entityId:'SERVICE-1',name:'database'},evidenceDetails:{details:[{eventId:'E-'+n,displayName:'Connection pool waits'}]}});
globalThis.fetch=async(url,options={})=>{
 const u=new URL(url);
 if(u.hostname==='cloudflare-dns.com')return Response.json({Answer:u.searchParams.get('type')==='A'?[{type:1,data:'93.184.215.14'}]:[]});
 if(u.hostname==='tenant.dynatrace.com'){
  assert.equal(options.headers.Authorization,'Api-Token dynatrace-fixture-secret');pageCalls.push(Object.fromEntries(u.searchParams));
  if(mode==='api-failure')return new Response('',{status:503});
  if(u.searchParams.has('nextPageKey')){assert.deepEqual([...u.searchParams.keys()].sort(),['fields','nextPageKey']);return Response.json({problems:[problem(2)],nextPageKey:null,totalCount:2})}
  return Response.json({problems:[problem(1)],nextPageKey:'page-two',totalCount:2});
 }
 if(u.hostname==='models.acme.com'){
  assert.equal(new Headers(options.headers).get('authorization'),'Bearer model-fixture-secret');const payload=JSON.parse(options.body);modelCalls.push(payload);lastMessages=payload.messages;
  if(mode==='model-failure')return new Response('private-provider-error',{status:500});
  if(payload.tools){const content=JSON.stringify(payload.messages);assert.match(content,/Knowledge Fabric|knowledgeSources|Initial knowledge/);if(!payload.messages.some(m=>m.role==='tool'))return Response.json({id:'tool',choices:[{index:0,finish_reason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[{id:'search',type:'function',function:{name:'search_knowledge',arguments:JSON.stringify({query:'database pool',approvedOnly:true})}}]}}]});}
  const text=payload.messages[0].content.includes('Draft an SOP')?'# Database recovery\nDraft based on verified resolution.':'Davis identifies database. Hypothesis needs checking. See approved SOP revision 1.';
  return Response.json({id:'fixture',object:'chat.completion',created:1,model:'configured-fixture-model',choices:[{index:0,finish_reason:'stop',message:{role:'assistant',content:text}}],usage:{prompt_tokens:20,completion_tokens:20,total_tokens:40}});
 }
 throw Error('Unexpected test fetch '+u);
};
try{
 const run=()=>api('/rca/workflows/'+flow+'/run','POST',{},operator);
 r=await run();assert.equal(r.status,200,JSON.stringify(await r.clone().json()));let result=await r.json();assert.equal(result.output.reportIds.length,1);assert.equal(result.output.coverage.hasNextPage,true);assert.equal(pageCalls[0].problemSelector,'status("open")');assert.equal(pageCalls[0].fields,'evidenceDetails,impactAnalysis');
 let s=await snapshot(),report=s.rcaReports.find(x=>x.id===result.output.reportIds[0]);assert.equal(report.problemId,'P-1');assert.equal(report.problem.evidenceDetails.details[0].eventId,'E-1');assert.deepEqual(report.sources.map(x=>x.id),[source]);assert.ok(!JSON.stringify(lastMessages).includes('unsafe draft'));
 r=await run();assert.equal(r.status,200);assert.equal((await r.json()).output.coverage.morePending,false);assert.equal((await snapshot()).rcaReports.length,2);
 const count=modelCalls.length;r=await run();assert.equal(r.status,200);assert.equal((await r.json()).output.skipped.length,1);assert.equal(modelCalls.length,count);
 // Complete this scan, then alter the approved SOP: new revisions require a new analysis.
 await run();s=await snapshot();const doc=s.documents.find(d=>d.id===source);await api('/documents/'+source,'PUT',{...doc,content:'Updated database pool diagnostic procedure.'});r=await run();assert.equal((await r.json()).output.reportIds.length,1);
 // Failure retains pending evidence and retries it without fetching a new page.
 mode='model-failure';r=await run();assert.equal(r.status,502);assert.ok(!JSON.stringify(await r.json()).includes('private-provider-error'));const pagesBefore=pageCalls.length;mode='normal';r=await run();assert.equal(r.status,200);assert.equal(pageCalls.length,pagesBefore);
 assert.equal((await api('/rca/reports/'+report.id+'/review','POST',{note:'Reviewed'},operator)).status,403);
 assert.equal((await api('/rca/reports/'+report.id+'/review','POST',{note:'Davis evidence checked; hypothesis remains provisional.'})).status,200);
 assert.equal((await api('/rca/reports/'+report.id+'/sop','POST',{resolved:true},operator)).status,400);
 const resolution={incidentReference:'INC-TEST-1',verifiedCause:'Confirmed connection leak',actions:'Rolled back affected release',validation:'Connection waits returned to measured baseline',resolved:true};
 r=await api('/rca/reports/'+report.id+'/sop','POST',resolution,operator);assert.equal(r.status,201);const sopId=(await r.json()).id;s=await snapshot();const sop=s.documents.find(d=>d.id===sopId);assert.equal(sop.status,'draft');assert.equal(sop.provenance.reportId,report.id);assert.match(sop.content,/INC-TEST-1/);assert.equal((await (await api('/rca/reports/'+report.id+'/sop','POST',resolution,operator)).json()).id,sopId);
 const agent=await create('agents',{mode:'reasoning',name:'Knowledge-aware agent',description:'Search SOPs',instruction:'Use search_knowledge for database pool procedures.',toolIds:[problemsTool],modelId:model,inputSchema:{type:'object',additionalProperties:true}});
 r=await api('/agents/'+agent+'/run','POST',{input:{symptom:'database pool'}},operator);assert.equal(r.status,200,JSON.stringify(await r.clone().json()));const agentRun=await r.json();assert.ok(agentRun.steps.some(s=>s.toolName==='Knowledge Fabric search'));assert.ok(agentRun.output.sources.some(s=>s.id===source&&s.revision===2));
 await api('/personas','POST',{userId:user.id,persona:'auditor'});assert.equal((await run()).status,403);assert.equal((await api('/knowledge/search','POST',{query:'database'},operator)).status,403);assert.equal((await api('/rca/reports/'+report.id+'/sop','POST',resolution,operator)).status,403);
 assert.ok(!JSON.stringify(await snapshot()).includes('fixture-secret'));
 // Throttling is bounded and honors long cooldowns without an early retry.
 let attempts=0;globalThis.fetch=async()=>++attempts===1?new Response('',{status:429,headers:{'Retry-After':'0'}}):new Response('ok');assert.equal((await fetchWithThrottleRetry('https://provider.acme.com',{})).status,200);assert.equal(attempts,2);
 attempts=0;globalThis.fetch=async()=>{attempts++;return new Response('',{status:429,headers:{'Retry-After':'60'}})};assert.equal((await fetchWithThrottleRetry('https://provider.acme.com',{})).status,429);assert.equal(attempts,1);
 console.log('PASS authenticated RCA configuration, exact Problems API pagination, approved SOP grounding, persisted evidence/citations, deduplication, revision reanalysis, failure resume, report review, verified-resolution SOP drafts, knowledge-aware reasoning, authorization and bounded throttling retries');
}finally{globalThis.fetch=actual;sqlite.close()}
