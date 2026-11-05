import {modelLimitError} from './model-transport.mjs';
import {searchKnowledge} from './knowledge.mjs';

const object=v=>v&&typeof v==='object'&&!Array.isArray(v);
function path(value,name,required=false){
 if(typeof value!=='string'||value.length>150||required&&!value||value&&!/^[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+){0,7}$/.test(value)||value.split('.').some(k=>['__proto__','constructor','prototype'].includes(k)))throw Error('Invalid '+name+' field path.');
 return value;
}
export function atPath(value,path){return path?path.split('.').reduce((v,k)=>v!=null&&Object.hasOwn(v,k)?v[k]:undefined,value):value}
export function normalizeAnalysis(v,field){
 if(!object(v.input)||JSON.stringify(v.input).length>10000||!object(v.cursorInput||{}))throw Error('Workflow input and cursor input must be JSON objects up to 10,000 characters.');
 if(JSON.stringify(v.cursorInput||{}).length>10000)throw Error('Cursor input is too large.');
 const cursorParameter=v.cursorParameter||'',nextPagePath=v.nextPagePath||'';
 if(typeof cursorParameter!=='string'||cursorParameter&&!/^[a-zA-Z][a-zA-Z0-9_]{0,79}$/.test(cursorParameter)||!!cursorParameter!==!!nextPagePath)throw Error('Configure both the next-page response path and cursor parameter, or leave both empty.');
 return {mode:'analysis',name:field(v,'name',100),description:field(v,'description',1000,false),toolId:field(v,'toolId',100),modelId:field(v,'modelId',100),instruction:field(v,'instruction',6000),input:v.input,itemsPath:path(v.itemsPath??'','items'),idPath:path(v.idPath,'record ID',true),titlePath:path(v.titlePath||'','title'),nextPagePath:path(nextPagePath,'next page'),cursorParameter,cursorInput:v.cursorInput||{},enabled:v.enabled!==false};
}
const hash=async v=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(v)))),x=>x.toString(16).padStart(2,'0')).join('');
const safeError=e=>modelLimitError(e)?.message||(/^(Model |Tool |The tool credential|Configure |Response exceeded|Invalid |Workflow |Selected |Record )/.test(e.message)?e.message:'Workflow execution failed or timed out. Inspect saved evidence before retrying.');
const boundedItem=item=>JSON.stringify(item).length<=10000?item:{excerpt:JSON.stringify(item).slice(0,10000),truncated:true};

export function makeAnalysisWorkflows({json,body,list,record,invoke,validate,log,analyze,normalize}){
 async function save(db,id,kind,value){await db.prepare('INSERT INTO workspace_records (id,kind,owner,value,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,revision=workspace_records.revision+1,updated_at=excluded.updated_at').bind(id,kind,'workspace',JSON.stringify(value),new Date().toISOString()).run()}
 async function check(env,flow){
  const tool=await record(env.DB,flow.toolId,'tools'),model=await record(env.DB,flow.modelId,'models');
  if(!tool||!tool.enabled||tool.method!=='GET'||tool.requiresApproval)throw Error('Selected analysis source must be an enabled read-only GET tool. Use an agent sequence for workflows that make changes.');
  if(!model?.endpoint||!model.secretRef)throw Error('Configure the selected model endpoint and credential reference.');
  validate(tool.inputSchema,flow.input);
  if(flow.cursorParameter){const type=tool.inputSchema.properties?.[flow.cursorParameter]?.type;validate(tool.inputSchema,{...flow.cursorInput,[flow.cursorParameter]:['integer','number'].includes(type)?0:'cursor'});}
  return {tool,model};
 }
 async function run(request,env,user,p,flow){
  if(!p.execute)return json({error:'Your persona cannot execute workflows.'},403);
  if(!flow.enabled)return json({error:'Workflow is disabled.'},409);
  let selected;try{selected=await check(env,flow)}catch(e){return json({error:e.message},409)}
  const id='exec-'+crypto.randomUUID(),stamp=Date.now(),deadline=stamp+150000;
  try{await env.DB.prepare('INSERT INTO workspace_runs (id,agent_id,requested_by,status,input,steps,started_at) VALUES (?,?,?,?,?,?,?)').bind(id,flow.id,user.id,'running',JSON.stringify(flow.input),'[]',new Date().toISOString()).run()}catch(e){if(String(e).includes('UNIQUE'))return json({error:'Workflow is already running.'},409);throw e}
  await log(env.DB,user,'Analysis workflow started',id);
  let steps=[],error=null,phase='Read source records',output={reportIds:[],skipped:[],coverage:null};
  try{
   const signature=await hash({flow,toolRevision:selected.tool.revision,modelRevision:selected.model.revision});
   const stateId='analysis-state-'+flow.id;
   let state=await record(env.DB,stateId,'analysis-state');
   if(!state||state.signature!==signature)state={signature,pending:[],cursor:null,scanStartedAt:null};
   if(!state.pending.length){
    if(state.cursor===null)state.scanStartedAt=new Date().toISOString();
    const input=state.cursor!==null?{...flow.cursorInput,[flow.cursorParameter]:state.cursor}:flow.input;
    const begin=Date.now(),page=await invoke(env,selected.tool,input,false,p,{signal:AbortSignal.timeout(deadline-Date.now())});
    const items=atPath(page,flow.itemsPath),cursor=flow.nextPagePath?atPath(page,flow.nextPagePath):null;
    if(!Array.isArray(items)||items.length>100)throw Error('Invalid records response. Configure an array path and a page size of at most 100.');
    if(cursor!=null&&cursor!==''&&!['string','number'].includes(typeof cursor))throw Error('Invalid pagination cursor in source response.');
    const entries=new Map();for(const item of items){const key=atPath(item,flow.idPath);if(!object(item)||!['string','number'].includes(typeof key)||String(key).length>200||String(key)==='')throw Error('Invalid record ID. Configure an ID path present on every record.');entries.set(String(key),item)}
    state.pending=[...entries.values()];state.cursor=cursor==null||cursor===''?null:cursor;
    // Persist the full page before making any model request.
    await save(env.DB,stateId,'analysis-state',state);
    steps.push({toolId:selected.tool.id,toolName:selected.tool.name,method:'GET',status:'succeeded',input,output:page,durationMs:Date.now()-begin});
    await env.DB.prepare('UPDATE workspace_runs SET steps=? WHERE id=?').bind(JSON.stringify(steps),id).run();
   }
   const docs=await list(env.DB,'documents');let processed=0;
   while(state.pending.length&&processed<2&&Date.now()<deadline-45000){
    const item=state.pending[0],itemId=String(atPath(item,flow.idPath)),rawTitle=flow.titlePath?atPath(item,flow.titlePath):itemId,title=String(rawTitle??itemId).slice(0,160);
    phase='Analyze record '+itemId;
    const sources=searchKnowledge(docs,title+' '+JSON.stringify(item).slice(0,10000),{approvedOnly:true,limit:2}).map(s=>({...s,content:s.content.slice(0,2000),excerptTruncated:s.excerptTruncated||s.content.length>2000}));
    const fingerprint=await hash({workflowId:flow.id,signature,item,sources:sources.map(s=>({id:s.id,revision:s.revision}))}),reportId='analysis-'+fingerprint;
    if(await record(env.DB,reportId,'analysis-reports'))output.skipped.push({itemId,reportId,reason:'Unchanged evidence and source revisions already analyzed.'});
    else{
     const begin=Date.now(),suppliedEvidence=boundedItem(item),analysis=await analyze(env,flow.modelId,{item:suppliedEvidence,itemId,sources,instruction:flow.instruction},deadline);
     await save(env.DB,reportId,'analysis-reports',{workflowId:flow.id,workflowName:flow.name,itemId,title,item,suppliedEvidence,sources,analysis,status:'draft',generatedAt:new Date().toISOString(),runId:id,fingerprint});
     output.reportIds.push(reportId);steps.push({toolName:'Knowledge-grounded analysis',status:'succeeded',input:{itemId},output:{reportId,analysis,sources:sources.map(s=>({id:s.id,revision:s.revision}))},durationMs:Date.now()-begin});
     await env.DB.prepare('UPDATE workspace_runs SET steps=? WHERE id=?').bind(JSON.stringify(steps),id).run();
    }
    state.pending.shift();processed++;await save(env.DB,stateId,'analysis-state',state);
   }
   output.coverage={scanStartedAt:state.scanStartedAt,morePending:state.pending.length>0||state.cursor!==null,pendingRecords:state.pending.length,hasNextPage:state.cursor!==null,note:'Coverage follows the configured tool input. Run the next batch to continue; a completed scan restarts on the next run.'};
  }catch(e){error=safeError(e);steps.push({toolName:phase,status:'failed',error})}
  const status=error?'failed':'succeeded';await env.DB.prepare('UPDATE workspace_runs SET status=?,output=?,error=?,steps=?,finished_at=?,duration_ms=? WHERE id=?').bind(status,JSON.stringify(output),error,JSON.stringify(steps),new Date().toISOString(),Date.now()-stamp,id).run();await log(env.DB,user,'Analysis workflow '+status,id);return json({id,status,output,error},error?502:200);
 }
 async function route(request,env,path,user,p){
  const migration=path.match(/^\/api\/workspace\/workflow\/migrate\/([a-z0-9-]+)$/);
  if(migration&&request.method==='POST'){
   if(!p.configure)return json({error:'Your persona cannot configure workflows.'},403);
   const previous=await record(env.DB,migration[1],'rca-workflows');if(!previous)return json({error:'Workflow was already migrated or is missing.'},409);
   const v=await body(request,30000);let value;try{value=normalize('orchestrations',{...v,mode:'analysis'});await check(env,value)}catch(e){return json({error:e.message},400)}
   const changed=await env.DB.prepare('UPDATE workspace_records SET kind=?,value=?,revision=revision+1,updated_at=? WHERE id=? AND kind=? AND revision=?').bind('orchestrations',JSON.stringify(value),new Date().toISOString(),previous.id,'rca-workflows',v.revision).run();
   if(!(changed.meta?.changes??changed.changes))return json({error:'Workflow changed. Refresh before saving.'},409);await log(env.DB,user,'Workflow generalized',previous.id);return json({id:previous.id});
  }
  const match=path.match(/^\/api\/workspace\/workflow\/reports\/([a-z0-9-]+)\/(review|sop)$/);
  if(!match||request.method!=='POST')return json({error:'Workflow endpoint not found.'},404);
  const report=await record(env.DB,match[1],'analysis-reports');if(!report)return json({error:'Report not found.'},404);
  if(match[2]==='review'){
   if(!p.approve)return json({error:'Administrator approval is required to review reports.'},403);
   const v=await body(request,3000);if(typeof v.note!=='string'||!v.note.trim()||v.note.length>2000)return json({error:'Enter a review note up to 2,000 characters.'},400);
   await save(env.DB,report.id,'analysis-reports',{...report,status:'reviewed',review:{note:v.note,by:user.id,at:new Date().toISOString()}});await log(env.DB,user,'Workflow report reviewed',report.id);return json({ok:true});
  }
  if(!p.configure||!p.execute)return json({error:'Your persona cannot draft procedures.'},403);
  const v=await body(request,9000);
  for(const k of ['incidentReference','verifiedCause','actions','validation'])if(typeof v[k]!=='string'||!v[k].trim()||v[k].length>1800)return json({error:'Provide a source reference, verified findings, performed actions and measured validation (up to 1,800 characters each).'},400);
  if(v.resolved!==true)return json({error:'Confirm the supplied outcome has been verified.'},400);
  const flow=await record(env.DB,report.workflowId,'orchestrations');if(!flow)return json({error:'Workflow no longer exists.'},409);
  try{
   const resolution={incidentReference:v.incidentReference,verifiedCause:v.verifiedCause,actions:v.actions,validation:v.validation,reportedBy:user.id};
   const sopId='sop-'+await hash({reportId:report.id,resolution});if(await record(env.DB,sopId,'documents'))return json({id:sopId,reused:true});
   const generated=await analyze(env,flow.modelId,{item:report.suppliedEvidence,itemId:report.itemId,sources:[],instruction:flow.instruction,resolution},Date.now()+45000);
   await save(env.DB,sopId,'documents',{title:('Procedure draft: '+report.title).slice(0,160),tags:'workflow,sop,draft',content:'# DRAFT — procedure\n\nUnapproved. Source record: '+report.itemId+'\nReference: '+v.incidentReference+'\n\n'+generated.text+'\n\n## Operator-verified outcome\n'+JSON.stringify(resolution,null,2),status:'draft',provenance:{reportId:report.id,resolution,model:generated.model}});await log(env.DB,user,'Workflow procedure drafted',sopId);return json({id:sopId},201);
  }catch(e){return json({error:safeError(e)},502)}
 }
 return {run,check,route};
}
