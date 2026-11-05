// Provider-neutral request limits. No provider response text or credentials are exposed.
export function modelPolicy(profile){
 const groq=profile.endpoint&&new URL(profile.endpoint).hostname==='api.groq.com';
 return {maxOutputTokens:profile.maxOutputTokens??1024,tokensPerMinute:profile.tokensPerMinute??(groq?8000:0),requestsPerMinute:profile.requestsPerMinute??(groq?30:0)};
}
export function normalizeModelLimits(value){
 const result={};
 for(const [key,min,max] of [['maxOutputTokens',128,8192],['tokensPerMinute',0,10000000],['requestsPerMinute',0,100000]]){
  if(value[key]===undefined)continue;
  if(!Number.isInteger(value[key])||value[key]<min||value[key]>max)throw Error('Invalid '+key+'.');
  result[key]=value[key];
 }
 return result;
}
export const estimateInputTokens=payload=>Math.ceil(new TextEncoder().encode(JSON.stringify({messages:payload.messages,tools:payload.tools,response_format:payload.response_format})).length/3);
export function modelLimitError(error){for(let e=error,i=0;e&&i<8;e=e.cause,i++)if(e.name==='ModelRateLimitError'||e.name==='ModelBudgetError')return e;return null}
function limitError(message,details={},name='ModelRateLimitError'){const e=new Error(message);e.name=name;e.details=details;return e}
export function durationMs(value,now=Date.now()){
 if(!value)return null;
 if(/^\d+(?:\.\d+)?$/.test(value))return Number(value)*1000;
 if(/^(?:\d+(?:\.\d+)?(?:h|ms|m|s))+$/.test(value)){let n=0;for(const [,v,u]of value.matchAll(/(\d+(?:\.\d+)?)(ms|h|m|s)/g))n+=Number(v)*({h:3600000,m:60000,s:1000,ms:1}[u]);return n}
 const date=Date.parse(value);return Number.isFinite(date)?Math.max(0,date-now):null;
}
export function rateLimitDetails(headers,text,now=Date.now()){
 let data={};try{data=JSON.parse(text)}catch{}
 const message=typeof data?.error?.message==='string'?data.error.message.slice(0,10000):'';
 const dimension=/tokens per day|\bTPD\b/i.test(message)?'tokens per day':/requests per day|\bRPD\b/i.test(message)?'requests per day':/tokens per minute|\bTPM\b/i.test(message)?'tokens per minute':/requests per minute|\bRPM\b/i.test(message)?'requests per minute':'provider quota';
 const number=key=>{const match=message.match(new RegExp('\\b'+key+'\\s*:?\\s*([0-9][0-9,.]*)','i'));const n=match?Number(match[1].replaceAll(',','')):NaN;return Number.isFinite(n)&&n>=0&&n<=1e12?n:null};
 const limit=number('Limit'),used=number('Used'),requested=number('Requested');
 const bodyWait=message.match(/try again in\s+([0-9.hms]+)/i)?.[1];
 const reset=dimension.includes('tokens')?headers.get('x-ratelimit-reset-tokens'):headers.get('x-ratelimit-reset-requests');
 const wait=durationMs(headers.get('retry-after'),now)??durationMs(bodyWait,now)??durationMs(reset,now);
 const requestTooLarge=/request too large|request_too_large/i.test(message)||limit!==null&&requested!==null&&requested>limit;
 return {status:429,quota:dimension,limit,used,requested,requestTooLarge,retryAfterSeconds:wait===null?null:Math.min(86400,Math.ceil(wait/1000))};
}
function describeLimit(d){
 const counts=[['limit',d.limit],['used',d.used],['requested',d.requested]].filter(([,n])=>n!==null).map(([k,n])=>k+' '+n).join(', ');
 return 'Model provider returned HTTP 429: '+d.quota+(counts?' ('+counts+')':'')+'. '+(d.requestTooLarge?'This request exceeds the allowance; reduce model input or maximum output tokens.':d.quota.includes('day')?'The daily quota is exhausted; wait for the provider reset.':d.retryAfterSeconds!==null?'Retry after '+d.retryAfterSeconds+' seconds.':'Check the provider Limits page for the reset time.');
}
export function sleepWithSignal(ms,signal){return new Promise((resolve,reject)=>{
 if(signal?.aborted){reject(signal.reason);return}
 const done=()=>{signal?.removeEventListener('abort',abort);resolve()},timer=setTimeout(done,ms);
 const abort=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);reject(signal.reason)};signal?.addEventListener('abort',abort,{once:true});
})}
const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),b=>b.toString(16).padStart(2,'0')).join('');

// Shared D1 reservations survive Worker restarts and serialize parallel callers by CAS.
export function makeModelTransport({db,profile,bounded,now=Date.now,sleep=sleepWithSignal,fetchImpl=(...args)=>fetch(...args)}){
 const policy=modelPolicy(profile);
 let budgetId;
 async function readBudget(){
  budgetId??='model-budget-'+await hash(new URL(profile.endpoint).origin+'|'+profile.model);
  await db.prepare('INSERT INTO workspace_records (id,kind,owner,value,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING').bind(budgetId,'model-budgets','system',JSON.stringify({windowStart:now(),tokens:0,requests:0,cooldownUntil:0}),new Date(now()).toISOString()).run();
  const row=await db.prepare('SELECT * FROM workspace_records WHERE id=? AND kind=?').bind(budgetId,'model-budgets').first();return {row,state:JSON.parse(row.value)};
 }
 async function changeBudget(row,state){const result=await db.prepare('UPDATE workspace_records SET value=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?').bind(JSON.stringify(state),new Date(now()).toISOString(),budgetId,row.revision).run();return !!(result.meta?.changes??result.changes)}
 async function reserve(tokens){
  if(policy.tokensPerMinute&&tokens>policy.tokensPerMinute)throw limitError('Model request needs approximately '+tokens+' input/output tokens, above the configured '+policy.tokensPerMinute+' tokens/minute allowance. Reduce selected tools, evidence or maximum output tokens.',{estimatedTokens:tokens,tokensPerMinute:policy.tokensPerMinute},'ModelBudgetError');
  for(let i=0;i<12;i++){
   const {row,state}=await readBudget(),time=now();if(time>=state.windowStart+60000){state.windowStart=time;state.tokens=0;state.requests=0}
   const windowFull=policy.tokensPerMinute&&state.tokens+tokens>policy.tokensPerMinute||policy.requestsPerMinute&&state.requests+1>policy.requestsPerMinute;
   const wait=Math.max(0,state.cooldownUntil-time,windowFull?state.windowStart+60000-time:0);
   if(wait>0)return {wait};
   state.tokens+=tokens;state.requests++;
   if(await changeBudget(row,state))return {windowStart:state.windowStart,tokens};
  }
  throw limitError('Model requests are busy. Retry after a few seconds.',{},'ModelBudgetError');
 }
 async function settle(reservation,actualTokens,cooldownUntil=0){
  for(let i=0;i<12;i++){const {row,state}=await readBudget();if(state.windowStart===reservation.windowStart&&Number.isFinite(actualTokens))state.tokens=Math.max(0,state.tokens-reservation.tokens+actualTokens);state.cooldownUntil=Math.max(state.cooldownUntil||0,cooldownUntil);if(await changeBudget(row,state))return}
 }
 return async function request(options){
  const payload=JSON.parse(options.body),supplied=payload.max_completion_tokens??payload.max_tokens??policy.maxOutputTokens;
  const maxOutput=Math.min(policy.maxOutputTokens,Number.isInteger(supplied)&&supplied>0?supplied:policy.maxOutputTokens);
  if(new URL(profile.endpoint).hostname==='api.groq.com'){delete payload.max_tokens;payload.max_completion_tokens=maxOutput}else{delete payload.max_completion_tokens;payload.max_tokens=maxOutput}
  const inputTokens=estimateInputTokens(payload),estimated=inputTokens+maxOutput;
  if(profile.context&&estimated>profile.context)throw limitError('Model request needs approximately '+estimated+' tokens, above the configured context window of '+profile.context+'. Reduce selected tools or evidence.',{estimatedTokens:estimated,context:profile.context},'ModelBudgetError');
  let waited=0;
  for(let attempt=0;attempt<2;attempt++){
   let reservation;
   for(let i=0;i<4;i++){options.signal?.throwIfAborted();reservation=await reserve(estimated);if(!reservation.wait)break;if(waited+reservation.wait>55000)throw limitError('Model quota cooldown is active. Retry after '+Math.ceil(reservation.wait/1000)+' seconds.',{retryAfterSeconds:Math.ceil(reservation.wait/1000)},'ModelBudgetError');await sleep(reservation.wait,options.signal);waited+=reservation.wait}
   if(!reservation||reservation.wait)throw limitError('Model quota is busy. Retry later.',{},'ModelBudgetError');
   const response=await fetchImpl(profile.endpoint,{...options,body:JSON.stringify(payload),redirect:'manual'});
   const text=await bounded(response,180000);
   if(response.status===429){
    const d=rateLimitDetails(response.headers,text,now()),delay=d.retryAfterSeconds===null?null:d.retryAfterSeconds*1000;
    await settle(reservation,0,d.requestTooLarge?0:now()+(delay??60000));
    if(attempt===0&&!d.requestTooLarge&&!d.quota.includes('day')&&delay!==null&&waited+delay<=55000){await sleep(delay,options.signal);waited+=delay;continue}
    throw limitError(describeLimit(d),d);
   }
   if(!response.ok)throw Error('Model provider returned HTTP '+response.status+'.');
   let actual;try{const usage=JSON.parse(text).usage;if(Number.isFinite(usage?.total_tokens)&&usage.total_tokens>=0)actual=usage.total_tokens}catch{}
   await settle(reservation,actual);
   return new Response(text,{status:response.status,headers:{'Content-Type':'application/json'}});
  }
 };
}

// Preserve the full response in execution history; send bounded structured evidence to the model.
export function compactToolResult(value,maxChars=3200){
 const original=JSON.stringify(value);if(original.length<=maxChars)return {value,truncated:false,originalChars:original.length};
 let truncated=false,budget=maxChars-500;
 const priorities=['problemId','entityId','eventId','id','displayId','title','name','displayName','status','rootCauseEntity','severityLevel','startTime','endTime','evidenceDetails','impactAnalysis','affectedEntities','impactedEntities','details','problems','entities','result','items'];
 function visit(v,depth){
  if(budget<40){truncated=true;return '[omitted: model context budget]'}
  if(typeof v==='string'){const n=Math.min(v.length,400,Math.max(0,budget-10));budget-=n+5;if(n<v.length)truncated=true;return v.slice(0,n)+(n<v.length?'…':'')}
  if(v===null||typeof v!=='object'){budget-=15;return v}
  if(depth>7){truncated=true;return '[omitted: nesting limit]'}
  if(Array.isArray(v)){const out=[];for(const x of v.slice(0,3)){if(budget<100){truncated=true;break}out.push(visit(x,depth+1))}if(out.length<v.length)truncated=true;return out}
  const out={},keys=Object.keys(v).sort((a,b)=>(priorities.includes(a)?priorities.indexOf(a):99)-(priorities.includes(b)?priorities.indexOf(b):99));
  for(const k of keys.slice(0,30)){if(budget<100){truncated=true;break}budget-=k.length+6;out[k]=visit(v[k],depth+1)}if(Object.keys(out).length<keys.length)truncated=true;return out;
 }
 let compact=visit(value,0);if(JSON.stringify(compact).length>maxChars){compact={excerpt:original.slice(0,Math.floor(maxChars/6))};truncated=true}
 return {value:compact,truncated:truncated||original!==JSON.stringify(compact),originalChars:original.length};
}
