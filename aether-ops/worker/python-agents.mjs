export function makePythonAgents({json,body,saveMetrics,initTelemetry}){
 async function init(db){for(const sql of [
 'CREATE TABLE IF NOT EXISTS python_agents (id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,gateway_id TEXT NOT NULL,agent_ref TEXT NOT NULL,mode TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)',
 'CREATE TABLE IF NOT EXISTS python_runs (id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,gateway_id TEXT NOT NULL,agent_ref TEXT NOT NULL,mode TEXT NOT NULL,input TEXT NOT NULL,status TEXT NOT NULL,output TEXT,error TEXT,requested_by TEXT NOT NULL,created_at INTEGER NOT NULL,finished_at TEXT)',
 'CREATE TABLE IF NOT EXISTS python_external_events (gateway_id TEXT NOT NULL,event_id TEXT NOT NULL,run_id TEXT NOT NULL,PRIMARY KEY(gateway_id,event_id))',
 'CREATE INDEX IF NOT EXISTS python_runs_gateway ON python_runs(gateway_id,status,created_at)',
 'CREATE UNIQUE INDEX IF NOT EXISTS python_runs_active ON python_runs(agent_id) WHERE status IN (\'queued\',\'running\')'])await db.prepare(sql).run();
 await db.prepare("UPDATE python_runs SET status='expired',error='Gateway did not complete within two minutes. Check the gateway before starting another run.',finished_at=? WHERE status IN ('queued','running') AND created_at<?").bind(new Date().toISOString(),Date.now()-120000).run();
 }
 async function pythonApi(request,env,path,user){
  const db=env.DB;await init(db);
  if(path==='/api/python-agents'&&request.method==='GET'){
   const agents=(await db.prepare('SELECT * FROM python_agents ORDER BY created_at DESC').all()).results;
   const runs=(await db.prepare('SELECT id,agent_id,status,error,requested_by,created_at,finished_at FROM python_runs ORDER BY created_at DESC LIMIT 100').all()).results;
   return json({agents,runs});
  }
  const runMatch=path.match(/^\/api\/python-agents\/runs\/(run-[a-z0-9-]+)$/);
  if(runMatch&&request.method==='GET'){
   const run=await db.prepare('SELECT * FROM python_runs WHERE id=?').bind(runMatch[1]).first();if(!run)return json({error:'Run not found.'},404);
   return json({run:{...run,input:JSON.parse(run.input),output:run.output===null?null:JSON.parse(run.output)}});
  }
  if(user.role!=='admin')return json({error:'Administrator access is required.'},403);
  if(path==='/api/python-agents'&&request.method==='POST'){
   const v=await body(request);if(typeof v.name!=='string'||!v.name.trim()||v.name.length>80||typeof v.description!=='string'||v.description.length>1000||typeof v.agentRef!=='string'||!/^[a-zA-Z0-9_-]{1,64}$/.test(v.agentRef)||!['observe','change'].includes(v.mode))return json({error:'Enter a name, description, valid local agent alias and execution mode.'},400);
   const gw=await db.prepare('SELECT id FROM infra_gateways WHERE id=? AND active=1').bind(v.gatewayId||'').first();if(!gw)return json({error:'Register and select an active infrastructure gateway first.'},400);
   const id='py-'+crypto.randomUUID();await db.prepare('INSERT INTO python_agents (id,name,description,gateway_id,agent_ref,mode,created_at) VALUES (?,?,?,?,?,?,?)').bind(id,v.name.trim(),v.description,v.gatewayId,v.agentRef,v.mode,new Date().toISOString()).run();return json({id},201);
  }
  const match=path.match(/^\/api\/python-agents\/(py-[a-z0-9-]+)(?:\/(run))?$/);
  if(match){
   const agent=await db.prepare('SELECT * FROM python_agents WHERE id=?').bind(match[1]).first();if(!agent)return json({error:'Agent not found.'},404);
   if(request.method==='PATCH'&&!match[2]){const v=await body(request);if(typeof v.enabled!=='boolean')return json({error:'Set enabled to true or false.'},400);await db.prepare('UPDATE python_agents SET enabled=? WHERE id=?').bind(v.enabled?1:0,agent.id).run();if(!v.enabled)await db.prepare("UPDATE python_runs SET status='cancelled',error='Agent disabled before execution.' WHERE agent_id=? AND status='queued'").bind(agent.id).run();return json({ok:true})}
   if(request.method==='POST'&&match[2]){
    if(!agent.enabled)return json({error:'This agent is disabled.'},409);
    const gw=await db.prepare('SELECT id FROM infra_gateways WHERE id=? AND active=1').bind(agent.gateway_id).first();if(!gw)return json({error:'Gateway access has been revoked.'},409);
    const v=await body(request,20000);if(!v.input||typeof v.input!=='object'||Array.isArray(v.input)||JSON.stringify(v.input).length>16000)return json({error:'Agent input must be a JSON object of at most 16,000 characters.'},400);
    if(agent.mode==='change'&&v.approved!==true)return json({error:'Explicit administrator approval is required for this run.'},403);
    const active=await db.prepare("SELECT id FROM python_runs WHERE agent_id=? AND status IN ('queued','running')").bind(agent.id).first();if(active)return json({error:'This agent already has a pending run.'},409);
    const id='run-'+crypto.randomUUID();try{await db.prepare('INSERT INTO python_runs (id,agent_id,gateway_id,agent_ref,mode,input,status,requested_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)').bind(id,agent.id,agent.gateway_id,agent.agent_ref,agent.mode,JSON.stringify(v.input),'queued',user.username,Date.now()).run()}catch(error){if(String(error).includes('UNIQUE'))return json({error:'This agent already has a pending run.'},409);throw error}return json({id,status:'queued'},202);
   }
  }
  return json({error:'Agent endpoint not found.'},404);
 }
 async function pollPython(db,gateway){
  await init(db);const run=await db.prepare("SELECT r.* FROM python_runs r JOIN python_agents a ON a.id=r.agent_id WHERE r.gateway_id=? AND r.status='queued' AND a.enabled=1 ORDER BY r.created_at LIMIT 1").bind(gateway.id).first();if(!run)return json({job:null});
  const claim=await db.prepare("UPDATE python_runs SET status='running' WHERE id=? AND status='queued'").bind(run.id).run();if(claim.meta?.changes===0||claim.changes===0)return json({job:null});
  return json({job:{kind:'python-agent',id:run.id,agentRef:run.agent_ref,mode:run.mode,input:JSON.parse(run.input)}});
 }
 async function resultPython(request,db,gateway){
  await init(db);const v=await body(request,24000);const run=await db.prepare("SELECT id FROM python_runs WHERE id=? AND gateway_id=? AND status='running'").bind(v.jobId||'',gateway.id).first();if(!run)return json({error:'Run is no longer active.'},409);
  if(typeof v.ok!=='boolean'||v.ok&&(v.output===undefined||JSON.stringify(v.output).length>16000))return json({error:'Invalid agent result or result exceeds 16,000 characters.'},400);
  const allowed=['timeout','invalid-output','execution-failed','not-configured','mode-mismatch'];
  const error=v.ok?null:allowed.includes(v.error)?v.error:'execution-failed';
  const completion=await db.prepare('UPDATE python_runs SET status=?,output=?,error=?,finished_at=? WHERE id=? AND status=?').bind(v.ok?'succeeded':'failed',v.ok?JSON.stringify(v.output):null,error,new Date().toISOString(),run.id,'running').run();if(completion.meta?.changes===0||completion.changes===0)return json({error:'Run is no longer active.'},409);await saveMetrics(db,run.id,v);return json({ok:true});
 }
 async function recordExternal(request,db,gateway){
  await init(db);await initTelemetry(db);const v=await body(request,40000);
  if(typeof v.executionId!=='string'||!/^[a-zA-Z0-9_.:-]{1,128}$/.test(v.executionId)||typeof v.agentRef!=='string'||typeof v.ok!=='boolean'||!v.input||typeof v.input!=='object'||Array.isArray(v.input)||JSON.stringify(v.input).length>16000||v.output===undefined||JSON.stringify(v.output).length>16000)return json({error:'Provide an executionId, agentRef, success status, JSON input and bounded output.'},400);
  const agent=await db.prepare('SELECT * FROM python_agents WHERE gateway_id=? AND agent_ref=? ORDER BY created_at LIMIT 1').bind(gateway.id,v.agentRef).first();if(!agent)return json({error:'Register this agent alias on the gateway first.'},404);
  const recorded=await db.prepare('SELECT run_id FROM python_external_events WHERE gateway_id=? AND event_id=?').bind(gateway.id,v.executionId).first();if(recorded){const existingRun=await db.prepare('SELECT id FROM python_runs WHERE id=?').bind(recorded.run_id).first();const existingMetrics=await db.prepare('SELECT run_id FROM python_run_metrics WHERE run_id=?').bind(recorded.run_id).first();if(existingRun&&existingMetrics)return json({id:recorded.run_id,alreadyRecorded:true})}
  const now=Date.now(),started=v.startedAt===undefined?now:Date.parse(v.startedAt),finished=v.finishedAt===undefined?now:Date.parse(v.finishedAt);
  if(!Number.isFinite(started)||!Number.isFinite(finished)||started>finished||finished>now+300000)return json({error:'Invalid execution timestamps.'},400);
  const id='run-'+crypto.randomUUID();await db.prepare('INSERT OR IGNORE INTO python_external_events (gateway_id,event_id,run_id) VALUES (?,?,?)').bind(gateway.id,v.executionId,id).run();
  const canonical=await db.prepare('SELECT run_id FROM python_external_events WHERE gateway_id=? AND event_id=?').bind(gateway.id,v.executionId).first();
  const inserted=await db.prepare('INSERT OR IGNORE INTO python_runs (id,agent_id,gateway_id,agent_ref,mode,input,status,output,error,requested_by,created_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)').bind(canonical.run_id,agent.id,gateway.id,agent.agent_ref,agent.mode,JSON.stringify(v.input),v.ok?'succeeded':'failed',JSON.stringify(v.output),v.ok?null:'External agent reported a failed execution.','external-ui',started,new Date(finished).toISOString()).run();
  const recordedMetrics=await db.prepare('SELECT run_id FROM python_run_metrics WHERE run_id=?').bind(canonical.run_id).first();if(!recordedMetrics)await saveMetrics(db,canonical.run_id,{...v,durationSource:'external'});
  return json({id:canonical.run_id,alreadyRecorded:canonical.run_id!==id},201);
 }

 return {pythonApi,pollPython,resultPython,initPython:init,recordExternal};
}
