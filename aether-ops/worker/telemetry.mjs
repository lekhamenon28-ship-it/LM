export function makeTelemetry({json}){
 async function init(db){await db.prepare('CREATE TABLE IF NOT EXISTS python_run_metrics (run_id TEXT PRIMARY KEY,duration_ms INTEGER,duration_source TEXT,input_tokens INTEGER,output_tokens INTEGER,cached_input_tokens INTEGER,cost REAL,currency TEXT,provider TEXT,model TEXT,telemetry_error TEXT)').run()}
 function normalize(v){
  const m={durationMs:null,inputTokens:null,outputTokens:null,cachedInputTokens:null,cost:null,currency:null,provider:null,model:null,error:null};
  if(v.durationMs!==undefined){if(Number.isSafeInteger(v.durationMs)&&v.durationMs>=0&&v.durationMs<=86400000)m.durationMs=v.durationMs;else m.error='Invalid execution duration was omitted.'}
  if(v.usage===undefined||v.usage===null)return m;
  const u=v.usage;if(typeof u!=='object'||Array.isArray(u)){m.error='Invalid agent usage was omitted.';return m}
  for(const key of ['inputTokens','outputTokens','cachedInputTokens'])if(u[key]!==undefined){if(Number.isSafeInteger(u[key])&&u[key]>=0&&u[key]<=1000000000)m[key]=u[key];else m.error='Invalid agent usage fields were omitted.'}
  if(m.cachedInputTokens!==null&&(m.inputTokens===null||m.cachedInputTokens>m.inputTokens)){m.cachedInputTokens=null;m.error='Invalid cached token count was omitted.'}
  if(u.cost!==undefined||u.currency!==undefined){if(typeof u.cost==='number'&&Number.isFinite(u.cost)&&u.cost>=0&&u.cost<=1000000&&typeof u.currency==='string'&&/^[A-Z]{3}$/.test(u.currency)){m.cost=u.cost;m.currency=u.currency}else m.error='Cost without a valid amount and currency was omitted.'}
  for(const key of ['provider','model'])if(u[key]!==undefined){if(typeof u[key]==='string'&&u[key].length<=100)m[key]=u[key];else m.error='Invalid model metadata was omitted.'}
  return m;
 }
 async function saveMetrics(db,id,v){await init(db);const m=normalize(v);await db.prepare('INSERT OR REPLACE INTO python_run_metrics (run_id,duration_ms,duration_source,input_tokens,output_tokens,cached_input_tokens,cost,currency,provider,model,telemetry_error) VALUES (?,?,?,?,?,?,?,?,?,?,?)').bind(id,m.durationMs,v.durationSource==='external'?'external':'gateway',m.inputTokens,m.outputTokens,m.cachedInputTokens,m.cost,m.currency,m.provider,m.model,m.error).run()}
 async function telemetryApi(request,env,path){
  if(path!=='/api/telemetry'||request.method!=='GET')return json({error:'Telemetry endpoint not found.'},404);
  const db=env.DB;await init(db);
  const summary=await db.prepare("SELECT COUNT(*) total,SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) succeeded,SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) expired,SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) pending,SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled FROM python_runs").first();for(const k in summary)summary[k]??=0;
  const agents=await db.prepare('SELECT COUNT(*) total,SUM(enabled) enabled FROM python_agents').first();agents.enabled??=0;
  const gateways=(await db.prepare('SELECT id,name,last_seen,active FROM infra_gateways').all()).results;
  const connections=(await db.prepare('SELECT id,name,product,status,checked_at,inventory FROM infra_connections').all()).results.map(c=>({...c,inventoryCount:JSON.parse(c.inventory).length,inventory:undefined}));
  const aggregate=await db.prepare('SELECT COUNT(duration_ms) durationCoverage,AVG(duration_ms) averageDurationMs,COUNT(input_tokens) inputCoverage,SUM(input_tokens) inputTokens,COUNT(output_tokens) outputCoverage,SUM(output_tokens) outputTokens,SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL THEN 1 ELSE 0 END) tokenCoverage,SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL THEN input_tokens+output_tokens END) totalTokens FROM python_run_metrics').first();aggregate.tokenCoverage??=0;
  const costs=(await db.prepare('SELECT currency,SUM(cost) amount,COUNT(*) runs FROM python_run_metrics WHERE cost IS NOT NULL GROUP BY currency').all()).results;
  const models=(await db.prepare('SELECT provider,model,COUNT(*) runs,SUM(input_tokens) inputTokens,SUM(output_tokens) outputTokens FROM python_run_metrics WHERE input_tokens IS NOT NULL OR output_tokens IS NOT NULL GROUP BY provider,model').all()).results;
  const runs=(await db.prepare('SELECT r.id,r.agent_id,a.name agent_name,r.status,r.created_at,r.finished_at,r.requested_by,r.error,m.duration_ms,m.duration_source,m.input_tokens,m.output_tokens,m.cached_input_tokens,m.cost,m.currency,m.provider,m.model,m.telemetry_error FROM python_runs r LEFT JOIN python_agents a ON a.id=r.agent_id LEFT JOIN python_run_metrics m ON m.run_id=r.id ORDER BY r.created_at DESC LIMIT 100').all()).results;
  return json({source:'persisted-agent-executions',generatedAt:new Date().toISOString(),scope:'All recorded runs; latest 100 shown',summary,agents,gateways,connections,aggregate,costs,models,runs});
 }
 return {saveMetrics,telemetryApi,initTelemetry:init};
}
