import {modelLimitError} from './model-transport.mjs';
import {searchKnowledge} from './knowledge.mjs';

export const problemsSchema={type:'object',properties:{problemSelector:{type:'string'},from:{type:'string'},to:{type:'string'},pageSize:{type:'integer'},sort:{type:'string'},fields:{type:'string'},nextPageKey:{type:'string'}},additionalProperties:false};
export function normalizeRcaWorkflow(v,field){
 return {name:field(v,'name',100),toolId:field(v,'toolId',100),modelId:field(v,'modelId',100),from:field(v,'from',80),enabled:v.enabled!==false};
}
const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)))),v=>v.toString(16).padStart(2,'0')).join('');
const boundedEvidence=(value,limit)=>{if(value===undefined)return null;const text=JSON.stringify(value);return text.length<=limit?value:{excerpt:text.slice(0,limit),truncated:true}};
const modelEvidence=problem=>({problemId:problem.problemId,title:problem.title,status:problem.status,startTime:problem.startTime,endTime:problem.endTime,rootCauseEntity:boundedEvidence(problem.rootCauseEntity,1000),affectedEntities:boundedEvidence(problem.affectedEntities,1000),impactedEntities:boundedEvidence(problem.impactedEntities,1000),evidenceDetails:boundedEvidence(problem.evidenceDetails,6000),impactAnalysis:boundedEvidence(problem.impactAnalysis,1500)});
const safeError=e=>modelLimitError(e)?.message||(/^(Model provider returned HTTP|Tool returned HTTP|Response exceeded|The tool credential|Configure |Model returned|Tool endpoint|Invalid Dynatrace|RCA |No usable|Selected )/.test(e.message)?e.message:'RCA request failed or timed out. Review execution evidence before retrying.');

export function makeRcaWorkflows({json,body,list,record,invoke,log,analyze}){
 async function save(db,id,kind,value){const now=new Date().toISOString();await db.prepare('INSERT INTO workspace_records (id,kind,owner,value,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,revision=workspace_records.revision+1,updated_at=excluded.updated_at').bind(id,kind,'workspace',JSON.stringify(value),now).run()}
 async function check(env,flow){
  const tool=await record(env.DB,flow.toolId,'tools'),model=await record(env.DB,flow.modelId,'models');
  if(!tool||!tool.enabled||tool.method!=='GET'||tool.requiresApproval||!new URL(tool.endpoint).pathname.endsWith('/api/v2/problems'))throw Error('Selected tool must be an enabled read-only Dynatrace /api/v2/problems GET tool.');
  for(const k of Object.keys(problemsSchema.properties))if(tool.inputSchema.properties?.[k]?.type!==problemsSchema.properties[k].type)throw Error('Selected Problems tool must support the complete Problems input schema.');
  if(tool.inputSchema.required?.length)throw Error('Selected Problems tool must allow pagination with only nextPageKey and fields.');
  if(!model?.endpoint||!model.secretRef)throw Error('Configure the selected model endpoint and credential reference.');
  return {tool,model};
 }
 async function run(request,env,user,p,flow){
  if(!p.execute)return json({error:'Your persona cannot execute RCA workflows.'},403);
  if(!flow.enabled)return json({error:'RCA workflow is disabled.'},409);
  let selected;try{selected=await check(env,flow)}catch(e){return json({error:e.message},409)}
  const id='exec-'+crypto.randomUUID(),started=new Date().toISOString(),stamp=Date.now(),deadline=stamp+150000;
  try{await env.DB.prepare('INSERT INTO workspace_runs (id,agent_id,requested_by,status,input,steps,started_at) VALUES (?,?,?,?,?,?,?)').bind(id,flow.id,user.id,'running',JSON.stringify({from:flow.from,toolId:flow.toolId,modelId:flow.modelId}),'[]',started).run()}catch(e){if(String(e).includes('UNIQUE'))return json({error:'RCA workflow is already running.'},409);throw e}
  await log(env.DB,user,'RCA workflow started',id);
  let steps=[],error=null,phase='RCA preparation',output={reportIds:[],skipped:[],coverage:null};
  const stateId='rca-state-'+flow.id;
  try{
   const signature=await hash({flow,toolRevision:selected.tool.revision,modelRevision:selected.model.revision});
   let state=await record(env.DB,stateId,'rca-state');
   if(!state||state.signature!==signature)state={signature,pending:[],nextPageKey:null,scanStartedAt:null};
   if(!state.pending.length){
    if(!state.nextPageKey)state.scanStartedAt=new Date().toISOString();
    const input=state.nextPageKey?{nextPageKey:state.nextPageKey,fields:'evidenceDetails,impactAnalysis'}:{problemSelector:'status("open")',from:flow.from,to:state.scanStartedAt,pageSize:2,sort:'-startTime',fields:'evidenceDetails,impactAnalysis'};
    phase='Dynatrace Problems fetch';const begin=Date.now();const page=await invoke(env,selected.tool,input,false,p,{signal:AbortSignal.timeout(Math.max(1,deadline-Date.now()))});
    if(!Array.isArray(page.problems)||page.problems.length>2||page.problems.some(x=>!x||typeof x.problemId!=='string')||page.nextPageKey!=null&&typeof page.nextPageKey!=='string')throw Error('Invalid Dynatrace Problems response.');
    state.pending=[...new Map(page.problems.map(problem=>[problem.problemId,problem])).values()];state.nextPageKey=page.nextPageKey||null;
    state.warnings=Array.isArray(page.warnings)?page.warnings:[];
    state.totalCount=page.totalCount??null;
    steps.push({toolId:selected.tool.id,toolName:selected.tool.name,status:'succeeded',method:'GET',input,output:page,durationMs:Date.now()-begin});
    await save(env.DB,stateId,'rca-state',state);
    await env.DB.prepare('UPDATE workspace_runs SET steps=? WHERE id=?').bind(JSON.stringify(steps),id).run();
   }
   const docs=await list(env.DB,'documents');
   while(state.pending.length){
    if(Date.now()>deadline-45000)break;
    const problem=state.pending[0];
    if(problem.status!=='OPEN'){output.skipped.push({problemId:problem.problemId,reason:'No longer open in the returned snapshot.'});state.pending.shift();await save(env.DB,stateId,'rca-state',state);continue}
    const sources=searchKnowledge(docs,JSON.stringify({title:problem.title,rootCauseEntity:problem.rootCauseEntity,evidenceDetails:problem.evidenceDetails,entityTags:problem.entityTags}).slice(0,12000),{approvedOnly:true,limit:2}).map(s=>({...s,content:s.content.slice(0,2000),excerptTruncated:s.excerptTruncated||s.content.length>2000}));
    const fingerprint=await hash({workflowId:flow.id,signature,problem,sources:sources.map(s=>({id:s.id,revision:s.revision}))});
    const reportId='rca-'+fingerprint;
    if(await record(env.DB,reportId,'rca-reports'))output.skipped.push({problemId:problem.problemId,reason:'Unchanged evidence and SOP revisions already analyzed.',reportId});
    else{
     phase='SOP-grounded RCA for '+problem.problemId;const begin=Date.now(),suppliedEvidence=modelEvidence(problem),analysis=await analyze(env,flow.modelId,{problem:suppliedEvidence,sources},deadline);
     const report={workflowId:flow.id,workflowName:flow.name,problemId:problem.problemId,displayId:problem.displayId||null,title:problem.title||problem.problemId,status:'draft',problem,suppliedEvidence,sources,analysis,generatedAt:new Date().toISOString(),runId:id,fingerprint};
     await save(env.DB,reportId,'rca-reports',report);output.reportIds.push(reportId);
     steps.push({toolName:'SOP-grounded RCA',status:'succeeded',input:{problemId:problem.problemId},output:{reportId,sources:sources.map(s=>({id:s.id,revision:s.revision})),analysis},durationMs:Date.now()-begin});
     await env.DB.prepare('UPDATE workspace_runs SET steps=? WHERE id=?').bind(JSON.stringify(steps),id).run();
    }
    state.pending.shift();await save(env.DB,stateId,'rca-state',state);
   }
   output.coverage={from:flow.from,to:state.scanStartedAt,morePending:state.pending.length>0||!!state.nextPageKey,pendingProblems:state.pending.length,hasNextPage:!!state.nextPageKey,totalCount:state.totalCount,warnings:state.warnings||[],note:'Coverage is limited to the configured time window. Older open problems may be excluded. Run the next batch to continue; after the final batch a new scan begins.'};
  }catch(e){error=safeError(e);steps.push({toolName:phase,status:'failed',error});}
  const status=error?'failed':'succeeded';
  await env.DB.prepare('UPDATE workspace_runs SET status=?,output=?,error=?,steps=?,finished_at=?,duration_ms=? WHERE id=?').bind(status,JSON.stringify(output),error,JSON.stringify(steps),new Date().toISOString(),Date.now()-stamp,id).run();
  await log(env.DB,user,'RCA workflow '+status,id);
  return json({id,status,output,error},error?502:200);
 }
 async function route(request,env,path,user,p){
  const runMatch=path.match(/^\/api\/workspace\/rca\/workflows\/([a-z0-9-]+)\/run$/);
  if(runMatch&&request.method==='POST'){const flow=await record(env.DB,runMatch[1],'rca-workflows');if(!flow)return json({error:'RCA workflow not found.'},404);return run(request,env,user,p,flow)}
  const reviewMatch=path.match(/^\/api\/workspace\/rca\/reports\/([a-z0-9-]+)\/review$/);
  if(reviewMatch&&request.method==='POST'){
   if(!p.approve)return json({error:'Administrator approval is required to review RCA reports.'},403);
   const v=await body(request,3000),report=await record(env.DB,reviewMatch[1],'rca-reports');if(!report)return json({error:'RCA report not found.'},404);
   if(typeof v.note!=='string'||!v.note.trim()||v.note.length>2000)return json({error:'Enter a review note up to 2,000 characters.'},400);
   await save(env.DB,report.id,'rca-reports',{...report,status:'reviewed',review:{note:v.note,by:user.id,at:new Date().toISOString()}});await log(env.DB,user,'RCA report reviewed',report.id);return json({ok:true});
  }
  const learnMatch=path.match(/^\/api\/workspace\/rca\/reports\/([a-z0-9-]+)\/sop$/);
  if(learnMatch&&request.method==='POST'){
   if(!p.configure||!p.execute)return json({error:'Your persona cannot draft SOPs.'},403);
   const v=await body(request,9000),report=await record(env.DB,learnMatch[1],'rca-reports');if(!report)return json({error:'RCA report not found.'},404);
   for(const k of ['incidentReference','verifiedCause','actions','validation'])if(typeof v[k]!=='string'||!v[k].trim()||v[k].length>1800)return json({error:'Provide incidentReference, verifiedCause, actions and validation (up to 1,800 characters each).'},400);
   if(v.resolved!==true)return json({error:'Confirm the incident is resolved before drafting an SOP.'},400);
   const flow=await record(env.DB,report.workflowId,'rca-workflows')||await record(env.DB,report.workflowId,'orchestrations');if(!flow)return json({error:'RCA workflow no longer exists.'},409);
   try{const resolution={incidentReference:v.incidentReference,verifiedCause:v.verifiedCause,actions:v.actions,validation:v.validation,resolved:true,reportedBy:user.id};const sopId='sop-'+await hash({reportId:report.id,resolution});if(await record(env.DB,sopId,'documents'))return json({id:sopId,reused:true});
    const generated=await analyze(env,flow.modelId,{problem:modelEvidence(report.problem),sources:[],resolution},Date.now()+45000);
    const content='# DRAFT — incident-derived SOP\n\nUnapproved. Source problem: '+report.problemId+'\nIncident reference: '+v.incidentReference+'\n\n'+generated.text+'\n\n## Operator-verified resolution record\n'+JSON.stringify(resolution,null,2);
    await save(env.DB,sopId,'documents',{title:('SOP draft: '+report.title).slice(0,160),tags:'dynatrace,rca,sop,draft',content,status:'draft',provenance:{reportId:report.id,resolution,model:generated.model}});await log(env.DB,user,'Incident SOP drafted',sopId);return json({id:sopId},201);
   }catch(e){return json({error:safeError(e)},502)}
  }
  if(path==='/api/workspace/rca/problems-tool'&&request.method==='POST'){
   if(user.role!=='admin')return json({error:'Administrator access is required to register a tool.'},403);
   const v=await body(request,2000),source=await record(env.DB,v.toolId,'tools');
   if(!source||source.method!=='GET'||!source.enabled||source.authType!=='api-token'||!source.secretRef)return json({error:'Selected tool must be an enabled Dynatrace API-token GET connection.'},400);
   const url=new URL(source.endpoint);if(!/\/api\/v2\/(entities|problems)$/.test(url.pathname))return json({error:'Selected tool must use a Dynatrace entities or problems endpoint.'},400);
   url.pathname=url.pathname.replace(/\/(entities|problems)$/,'/problems');
   const id='dynatrace-problems-'+await hash({endpoint:url.toString(),secretRef:source.secretRef});if(await record(env.DB,id,'tools'))return json({id,reused:true});
   await save(env.DB,id,'tools',{...source,name:'Dynatrace Problems with Davis Evidence',description:'Read open Dynatrace problems with root-cause evidence and impact analysis. Requires problems.read scope.',endpoint:url.toString(),inputSchema:problemsSchema,requiresApproval:false,enabled:true});await log(env.DB,user,'Dynatrace Problems tool registered',id);return json({id},201);
  }
  return json({error:'RCA endpoint not found.'},404);
 }
 return {route,check};
}
