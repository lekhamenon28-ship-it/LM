export function makeInfrastructure({json,body,digest,random,pollPython,resultPython,recordExternal}){
const products=[['netapp','NetApp ONTAP','REST'],['cohesity','Cohesity','Custom adapter'],['vmware','VMware vCenter','REST'],['dynatrace','Dynatrace Classic Environment','REST'],['nutanix','Nutanix','Custom adapter'],['servicenow','ServiceNow','REST'],['kubernetes','Kubernetes','REST'],['sql','SQL databases','Custom adapter'],['linux','Linux','Custom adapter'],['windows','Windows','Custom adapter'],['aws','AWS','Custom adapter'],['azure','Microsoft Azure','Custom adapter'],['gcp','Google Cloud','Custom adapter'],['custom','Other infrastructure product','Custom adapter']].map(([id,name,adapter])=>({id,name,adapter}));
async function infraInit(db){for(const sql of [
'CREATE TABLE IF NOT EXISTS infra_gateways (id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT NOT NULL,last_seen TEXT,active INTEGER NOT NULL DEFAULT 1)',
'CREATE TABLE IF NOT EXISTS infra_connections (id TEXT PRIMARY KEY,name TEXT NOT NULL,product TEXT NOT NULL,gateway_id TEXT NOT NULL,credential_ref TEXT NOT NULL,status TEXT NOT NULL DEFAULT \'Not tested\',checked_at TEXT,inventory TEXT NOT NULL DEFAULT \'[]\',message TEXT)',
'CREATE TABLE IF NOT EXISTS infra_jobs (id TEXT PRIMARY KEY,connection_id TEXT NOT NULL,gateway_id TEXT NOT NULL,operation TEXT NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,claimed_at INTEGER)'])await db.prepare(sql).run()}
async function infraAgent(request,env,path){
 const db=env.DB;await infraInit(db);const token=request.headers.get('Authorization')?.match(/^Bearer ([a-f0-9]{64})$/)?.[1];if(!token)return json({error:'Gateway authentication required.'},401);
 const gateway=await db.prepare('SELECT * FROM infra_gateways WHERE token_hash=? AND active=1').bind(await digest(token)).first();if(!gateway)return json({error:'Gateway authentication failed.'},401);
 await db.prepare('UPDATE infra_gateways SET last_seen=? WHERE id=?').bind(new Date().toISOString(),gateway.id).run();
 if(path==='/api/gateway/poll'&&request.method==='POST'){
  const options=await body(request);
  await db.prepare("UPDATE infra_jobs SET status='expired' WHERE gateway_id=? AND status IN ('queued','running') AND created_at<?").bind(gateway.id,Date.now()-120000).run();
  const job=await db.prepare("SELECT j.*,c.product,c.credential_ref FROM infra_jobs j JOIN infra_connections c ON c.id=j.connection_id WHERE j.gateway_id=? AND j.status='queued' ORDER BY j.created_at LIMIT 1").bind(gateway.id).first();
  if(!job)return Array.isArray(options.capabilities)&&options.capabilities.includes('python-agents')?pollPython(db,gateway):json({job:null});
  const claim=await db.prepare("UPDATE infra_jobs SET status='running',claimed_at=? WHERE id=? AND status='queued'").bind(Date.now(),job.id).run();if(claim.meta?.changes===0||claim.changes===0)return json({job:null});
  return json({job:{id:job.id,operation:job.operation,product:job.product,credentialRef:job.credential_ref}});
 }
 if(path==='/api/gateway/python/record'&&request.method==='POST')return recordExternal(request,db,gateway);
 if(path==='/api/gateway/python/result'&&request.method==='POST')return resultPython(request,db,gateway);
 if(path==='/api/gateway/result'&&request.method==='POST'){
  const value=await body(request,32768);const job=await db.prepare("SELECT * FROM infra_jobs WHERE id=? AND gateway_id=? AND status='running' AND created_at>?").bind(value.jobId||'',gateway.id,Date.now()-120000).first();if(!job)return json({error:'Job is no longer active.'},409);
  if(typeof value.ok!=='boolean'||!Array.isArray(value.items)||value.items.length>50||value.items.some(x=>!x||typeof x!=='object'||Array.isArray(x)))return json({error:'Invalid gateway result.'},400);
  const items=value.items.map(x=>({id:String(x.id||'').slice(0,120),name:String(x.name||'').slice(0,180),type:String(x.type||'resource').slice(0,80),status:String(x.status||'unknown').slice(0,80)}));
  await db.prepare('UPDATE infra_connections SET status=?,checked_at=?,message=?,inventory=CASE WHEN ? THEN ? ELSE inventory END WHERE id=?').bind(value.ok?'Connected':'Failed',new Date().toISOString(),value.ok?'Read-only API request succeeded.':'Gateway request failed. Check the local gateway log, endpoint, certificate and permissions.',value.ok&&job.operation==='inventory'?1:0,JSON.stringify(items),job.connection_id).run();
  await db.prepare("UPDATE infra_jobs SET status=? WHERE id=?").bind(value.ok?'succeeded':'failed',job.id).run();return json({ok:true});
 }
 return json({error:'Gateway endpoint not found.'},404);
}
async function infraApi(request,env,path,user){
 const db=env.DB;await infraInit(db);
 if(path==='/api/infrastructure'&&request.method==='GET'){
  const gateways=(await db.prepare('SELECT id,name,last_seen,active FROM infra_gateways').all()).results;
  const connections=(await db.prepare('SELECT * FROM infra_connections').all()).results.map(c=>({...c,inventory:JSON.parse(c.inventory)}));
  const jobs=(await db.prepare("SELECT id,connection_id,status,created_at FROM infra_jobs ORDER BY created_at DESC LIMIT 100").all()).results;
  return json({products,gateways,connections,jobs});
 }
 if(user.role!=='admin')return json({error:'Administrator access is required.'},403);
 if(path==='/api/infrastructure/gateways'&&request.method==='POST'){
  const value=await body(request);if(typeof value.name!=='string'||!value.name.trim()||value.name.length>80)return json({error:'Enter a gateway name.'},400);
  const token=random(32),id='gw-'+crypto.randomUUID();await db.prepare('INSERT INTO infra_gateways (id,name,token_hash) VALUES (?,?,?)').bind(id,value.name.trim(),await digest(token)).run();return json({id,token},201);
 }
 if(path==='/api/infrastructure/connections'&&request.method==='POST'){
  const v=await body(request);if(typeof v.name!=='string'||!v.name.trim()||v.name.length>80||!products.some(p=>p.id===v.product)||typeof v.credentialRef!=='string'||!/^[a-zA-Z0-9_-]{1,64}$/.test(v.credentialRef))return json({error:'Enter a name, product and valid local configuration alias.'},400);
  const gateway=await db.prepare('SELECT id FROM infra_gateways WHERE id=? AND active=1').bind(v.gatewayId||'').first();if(!gateway)return json({error:'Select an active gateway.'},400);
  const id='conn-'+crypto.randomUUID();await db.prepare('INSERT INTO infra_connections (id,name,product,gateway_id,credential_ref) VALUES (?,?,?,?,?)').bind(id,v.name.trim(),v.product,v.gatewayId,v.credentialRef).run();return json({id},201);
 }
 const match=path.match(/^\/api\/infrastructure\/connections\/(conn-[a-z0-9-]+)(?:\/(test|inventory))?$/);
 if(match){const connection=await db.prepare('SELECT * FROM infra_connections WHERE id=?').bind(match[1]).first();if(!connection)return json({error:'Connection not found.'},404);
  if(request.method==='DELETE'&&!match[2]){await db.prepare('DELETE FROM infra_jobs WHERE connection_id=?').bind(connection.id).run();await db.prepare('DELETE FROM infra_connections WHERE id=?').bind(connection.id).run();return json({ok:true})}
  if(request.method==='POST'&&match[2]){
   const existing=await db.prepare("SELECT id FROM infra_jobs WHERE connection_id=? AND status IN ('queued','running') AND created_at>?").bind(connection.id,Date.now()-120000).first();if(existing)return json({error:'A request is already pending for this connection.'},409);
   const id='job-'+crypto.randomUUID();await db.prepare('INSERT INTO infra_jobs (id,connection_id,gateway_id,operation,status,created_at) VALUES (?,?,?,?,?,?)').bind(id,connection.id,connection.gateway_id,match[2],'queued',Date.now()).run();return json({id,status:'queued'},202);
  }
 }
 const gatewayMatch=path.match(/^\/api\/infrastructure\/gateways\/(gw-[a-z0-9-]+)$/);if(gatewayMatch&&request.method==='DELETE'){await db.prepare('UPDATE infra_gateways SET active=0 WHERE id=?').bind(gatewayMatch[1]).run();return json({ok:true})}
 return json({error:'Infrastructure endpoint not found.'},404);
}

return {infraApi,infraAgent,infraInit};
}
