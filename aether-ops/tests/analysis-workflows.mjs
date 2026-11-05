import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import worker from '../dist/server/index.js';
const sqlite=new DatabaseSync(':memory:');
const DB={prepare(sql){let args=[];return {bind(...v){args=v;return this},async run(){return {meta:{changes:Number(sqlite.prepare(sql).run(...args).changes)}}},async first(){return sqlite.prepare(sql).get(...args)||null},async all(){return {results:sqlite.prepare(sql).all(...args)}}}}};
const env={DB,BOOTSTRAP_ADMIN_USERNAME:'admin',BOOTSTRAP_ADMIN_PASSWORD:'workflow-test-password',CREDENTIAL_ENCRYPTION_KEY:'e'.repeat(64),ASSETS:{fetch:async()=>new Response('')}};
const origin='https://app.test';
const call=(path,method='GET',data,cookie)=>worker.fetch(new Request(origin+path,{method,headers:{Origin:origin,...(data?{'Content-Type':'application/json'}:{}),...(cookie?{Cookie:cookie}:{})},...(data?{body:JSON.stringify(data)}:{})}),env);
const login=async(username,password)=>{const r=await call('/api/auth/login','POST',{username,password});assert.equal(r.status,200);return r.headers.get('Set-Cookie').split(';')[0]};
const admin=await login('admin','workflow-test-password');
const api=(path,method='POST',data={},cookie=admin)=>call('/api/workspace'+path,method,data,cookie);
async function create(kind,data){const r=await api('/'+kind,'POST',data);assert.equal(r.status,201,JSON.stringify(await r.clone().json()));return (await r.json()).id}
const snapshot=async()=>(await call('/api/workspace','GET',null,admin)).json();
await api('/credentials','POST',{name:'MODEL_TEST',secret:'isolated-provider-key'});
const model=await create('models',{name:'Selected model',provider:'Fixture',model:'configured-model',endpoint:'https://models.acme.com/chat/completions',secretRef:'MODEL_TEST',context:8192,purpose:'Multiple use cases'});
const ticketSource=await create('documents',{title:'Service desk database procedure',tags:'database,tickets',content:'Review service impact before proposing ticket priority.',status:'approved'});
await create('documents',{title:'Capacity planning policy',tags:'capacity,cpu',content:'Check sustained CPU utilization before proposing capacity.',status:'approved'});
await create('documents',{title:'Unreviewed capacity draft',tags:'capacity',content:'Unvalidated',status:'draft'});
const ticketTool=await create('tools',{name:'Tickets',description:'Read incidents',endpoint:'https://tools.acme.com/tickets',method:'GET',secretRef:'',inputSchema:{type:'object',properties:{cursor:{type:'integer'},limit:{type:'integer'}},additionalProperties:false}});
const capacityTool=await create('tools',{name:'Capacity',description:'Read capacity records',endpoint:'https://tools.acme.com/capacity',method:'GET',secretRef:'',inputSchema:{type:'object',additionalProperties:true}});
const common={mode:'analysis',description:'',modelId:model,instruction:'Triage service desk tickets and cite approved procedures.',input:{limit:2},itemsPath:'data.items',idPath:'number',titlePath:'summary',nextPagePath:'next',cursorParameter:'cursor',cursorInput:{limit:2}};
const tickets=await create('orchestrations',{...common,name:'Service desk triage',toolId:ticketTool});
const capacity=await create('orchestrations',{...common,name:'Capacity review',toolId:capacityTool,instruction:'Review CPU capacity against approved capacity policy.',itemsPath:'resources',idPath:'id',titlePath:'name',nextPagePath:'',cursorParameter:'',cursorInput:{}});
assert.equal((await api('/orchestrations','POST',{...common,name:'Unsafe path',toolId:ticketTool,itemsPath:'__proto__.items'})).status,400);
assert.equal((await api('/orchestrations','POST',{...common,name:'Missing cursor',toolId:ticketTool,cursorParameter:''})).status,400);
assert.equal((await api('/tools/'+ticketTool,'DELETE')).status,409);
assert.equal((await api('/models/'+model,'DELETE')).status,409);
assert.equal((await api('/orchestrations/'+tickets+'/run','POST',{},null)).status,401);
const original=globalThis.fetch;let modelRequests=[],toolRequests=[],mode='normal';
globalThis.fetch=async(url,options={})=>{
 const u=new URL(url);
 if(u.hostname==='cloudflare-dns.com')return Response.json({Answer:u.searchParams.get('type')==='A'?[{type:1,data:'93.184.215.14'}]:[]});
 if(u.hostname==='tools.acme.com'){
  toolRequests.push(u);if(u.pathname==='/capacity')return Response.json({resources:[{id:'HOST-1',name:'CPU capacity',cpu:91}]});
  if(u.searchParams.has('cursor')){assert.equal(u.searchParams.get('cursor'),'0');return Response.json({data:{items:[{number:'INC-2',summary:'Database connection failures'}]},next:null})}
  return Response.json({data:{items:[{number:'INC-1',summary:'Database connection failures'}]},next:0});
 }
 if(u.hostname==='models.acme.com'){
  const data=JSON.parse(options.body);modelRequests.push(data);if(mode==='failure')return new Response('sensitive-provider-body',{status:500});
  return Response.json({id:'fixture',choices:[{index:0,finish_reason:'stop',message:{role:'assistant',content:'Evidence-based workflow result with approved source citations.'}}],usage:{prompt_tokens:20,completion_tokens:20,total_tokens:40}});
 }
 throw Error('Unexpected fetch '+u);
};
try{
 const run=id=>api('/orchestrations/'+id+'/run','POST',{input:{}});
 let r=await run(tickets);assert.equal(r.status,200,JSON.stringify(await r.clone().json()));const first=await r.json();assert.equal(first.output.coverage.hasNextPage,true);
 let s=await snapshot(),report=s.workflowReports.find(x=>x.id===first.output.reportIds[0]);assert.equal(report.itemId,'INC-1');assert.deepEqual(report.sources.map(x=>x.id),[ticketSource]);assert.equal(report.legacy,false);assert.match(modelRequests[0].messages[0].content,/Triage service desk/);assert.ok(!JSON.stringify(modelRequests[0]).includes('Dynatrace'));
 r=await run(tickets);assert.equal(r.status,200);assert.equal((await r.json()).output.coverage.morePending,false);assert.equal(toolRequests.at(-1).searchParams.get('cursor'),'0');
 const calls=modelRequests.length;r=await run(tickets);assert.equal((await r.json()).output.skipped.length,1);assert.equal(modelRequests.length,calls);
 r=await run(capacity);assert.equal(r.status,200);const cap=(await r.json()).output.reportIds[0];s=await snapshot();assert.equal(s.workflowReports.find(x=>x.id===cap).itemId,'HOST-1');assert.match(modelRequests.at(-1).messages[0].content,/CPU capacity/);assert.ok(!JSON.stringify(modelRequests.at(-1)).includes('Unreviewed capacity draft'));
 // A changed source revision triggers new analysis; failed model calls preserve pending records.
 const doc=s.documents.find(x=>x.id===ticketSource);await api('/documents/'+doc.id,'PUT',{...doc,content:'Updated database service desk procedure.'});mode='failure';r=await run(tickets);assert.equal(r.status,502);assert.ok(!JSON.stringify(await r.json()).includes('sensitive-provider-body'));const before=toolRequests.length;mode='normal';r=await run(tickets);assert.equal(r.status,200);assert.equal(toolRequests.length,before);
 assert.equal((await api('/workflow/reports/'+report.id+'/review','POST',{note:'Evidence checked.'})).status,200);
 r=await api('/workflow/reports/'+report.id+'/sop','POST',{incidentReference:'INC-1',verifiedCause:'Connection leak confirmed',actions:'Release rollback',validation:'Error rate recovered',resolved:true});assert.equal(r.status,201);s=await snapshot();const sop=s.documents.find(x=>x.title.startsWith('Procedure draft:'));assert.equal(sop.status,'draft');assert.equal(sop.provenance.reportId,report.id);
 // Existing agent sequences continue to execute alongside analysis workflows.
 const agent=await create('agents',{name:'Observe capacity',description:'',steps:[{toolId:capacityTool,inputFrom:'input'}]});const sequence=await create('orchestrations',{name:'Capacity agent sequence',description:'',stages:[{agentId:agent,inputFrom:'input'}]});r=await run(sequence);assert.equal(r.status,200);assert.equal((await r.json()).steps.length,1);
 // Legacy workflows can be edited into the shared workflow store without changing IDs.
 const entities=await create('tools',{name:'Existing Dynatrace',description:'',endpoint:'https://tools.acme.com/api/v2/entities',method:'GET',authType:'api-token',secretRef:'TOOL_DT',inputSchema:{type:'object',additionalProperties:true}});
 r=await api('/rca/problems-tool','POST',{toolId:entities});const dt=(await r.json()).id;
 const legacy=await create('rca-workflows',{name:'Existing incident analysis',toolId:dt,modelId:model,from:'now-30d'});
 const old=(await snapshot()).rcaWorkflows.find(x=>x.id===legacy);
 r=await api('/workflow/migrate/'+legacy,'POST',{...common,name:'Migrated incident workflow',toolId:ticketTool,revision:old.revision});assert.equal(r.status,200,JSON.stringify(await r.clone().json()));s=await snapshot();assert.ok(!s.rcaWorkflows.some(x=>x.id===legacy));assert.equal(s.orchestrations.find(x=>x.id===legacy).mode,'analysis');assert.equal((await api('/workflow/migrate/'+legacy,'POST',{...common,name:'Replay',toolId:ticketTool,revision:old.revision})).status,409);
 await call('/api/users','POST',{username:'auditor',password:'auditor-test-password'},admin);const observer=await login('auditor','auditor-test-password');const u=(await snapshot()).users.find(x=>x.username==='auditor');await api('/personas','POST',{userId:u.id,persona:'auditor'});assert.equal((await api('/orchestrations/'+tickets+'/run','POST',{},observer)).status,403);assert.equal((await api('/workflow/reports/'+report.id+'/review','POST',{note:'No'},observer)).status,403);
 console.log('PASS reusable ticket/capacity workflows, independent goals/tools/models/mappings, numeric-cursor pagination, knowledge grounding, saved reports/procedures, deduplication, failure resume, agent-sequence coexistence, legacy migration and authorization');
}finally{globalThis.fetch=original;sqlite.close()}
