import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {makeModelTransport,modelPolicy,normalizeModelLimits,compactToolResult,rateLimitDetails,durationMs,modelLimitError,sleepWithSignal} from '../worker/model-transport.mjs';
const sqlite=new DatabaseSync(':memory:');
sqlite.exec('CREATE TABLE workspace_records (id TEXT PRIMARY KEY,kind TEXT NOT NULL,owner TEXT NOT NULL,value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)');
const db={prepare(sql){let args=[];return {bind(...v){args=v;return this},async run(){return {meta:{changes:Number(sqlite.prepare(sql).run(...args).changes)}}},async first(){return sqlite.prepare(sql).get(...args)||null}}}};
const bounded=async response=>response.text();
let clock=1790000000000,waits=[];
const now=()=>clock,sleep=async ms=>{waits.push(ms);clock+=ms};
const profile=(model,extras={})=>({endpoint:'https://api.groq.com/openai/v1/chat/completions',model,context:8192,...extras});
const options=(data={})=>({method:'POST',headers:{Authorization:'Bearer test-private-key'},body:JSON.stringify({model:'test',messages:[{role:'user',content:'Investigate database incident'}],...data})});
const completed=()=>Response.json({choices:[{message:{content:'Verified evidence summary'}}],usage:{total_tokens:40}});
function transport(model,fetchImpl,extras={}){return makeModelTransport({db,profile:profile(model,extras),bounded,now,sleep,fetchImpl})}
try{
 assert.deepEqual(modelPolicy(profile('test')),{maxOutputTokens:1024,tokensPerMinute:8000,requestsPerMinute:30});
 assert.deepEqual(modelPolicy({endpoint:'https://models.acme.com/chat',maxOutputTokens:256,tokensPerMinute:0,requestsPerMinute:0}),{maxOutputTokens:256,tokensPerMinute:0,requestsPerMinute:0});
 for(const v of [{maxOutputTokens:0},{tokensPerMinute:-1},{requestsPerMinute:1.2}])assert.throws(()=>normalizeModelLimits(v),/Invalid/);
 assert.equal(durationMs('1m2.5s',clock),62500);assert.equal(durationMs('0',clock),0);assert.equal(durationMs(new Date(clock+2000).toUTCString(),clock),2000);
 const evidence={problems:[{problemId:'P-1',title:'Database timeouts',rootCauseEntity:{entityId:'SERVICE-1'},evidenceDetails:{details:[{eventId:'E-1',message:'Important symptom'}]},bulky:'x'.repeat(40000)}],nextPageKey:'page2'};
 const full=JSON.stringify(evidence),compact=compactToolResult(evidence);assert.equal(JSON.stringify(evidence),full);assert.equal(compact.truncated,true);assert.ok(JSON.stringify(compact.value).length<=3200);assert.match(JSON.stringify(compact.value),/P-1/);assert.match(JSON.stringify(compact.value),/SERVICE-1/);assert.match(JSON.stringify(compact.value),/E-1/);
 let calls=[];
 const request=transport('output-cap',async(url,o)=>{calls.push(JSON.parse(o.body));return completed()});
 await request(options());assert.equal(calls[0].max_completion_tokens,1024);assert.equal(calls[0].max_tokens,undefined);
 await request(options({max_tokens:30000}));assert.equal(calls[1].max_completion_tokens,1024);
 await request(options({max_tokens:256}));assert.equal(calls[2].max_completion_tokens,256);
 const other=makeModelTransport({db,profile:{...profile('other'),endpoint:'https://models.acme.com/chat'},bounded,now,sleep,fetchImpl:async(url,o)=>{const body=JSON.parse(o.body);assert.equal(body.max_tokens,1024);assert.equal(body.max_completion_tokens,undefined);return completed()}});await other(options());
 let sent=0;const oversized=transport('oversized',async()=>{sent++;return completed()},{tokensPerMinute:500,maxOutputTokens:256});await assert.rejects(()=>oversized(options({messages:[{role:'user',content:'x'.repeat(3000)}]})),/above the configured 500 tokens/);assert.equal(sent,0);
 // Per-minute budgets persist across transport instances and settle actual usage.
 waits=[];const pacedProfile=profile('paced',{requestsPerMinute:2});const makePaced=()=>makeModelTransport({db,profile:pacedProfile,bounded,now,sleep,fetchImpl:async()=>completed()});await makePaced()(options());await makePaced()(options());clock+=10000;await makePaced()(options());assert.deepEqual(waits,[50000]);
 // A short provider cooldown is honored once; a daily quota is never retried.
 let attempt=0;waits=[];const retry=transport('short-retry',async()=>++attempt===1?Response.json({error:{message:'Rate limit reached on tokens per minute (TPM): Limit 8000, Used 7900, Requested 500.'}},{status:429,headers:{'Retry-After':'3'}}):completed());await retry(options());assert.equal(attempt,2);assert.deepEqual(waits,[3000]);
 attempt=0;waits=[];const daily=transport('daily-limit',async()=>{attempt++;return Response.json({error:{message:'Rate limit reached on tokens per day (TPD): Limit 200000, Used 200000, Requested 500. org-private-id test-private-key'}},{status:429,headers:{'Retry-After':'3600'}})});
 await assert.rejects(()=>daily(options()),e=>{assert.match(e.message,/daily quota is exhausted/);assert.ok(!e.message.includes('org-private-id'));assert.ok(!e.message.includes('test-private-key'));assert.equal(e.details.quota,'tokens per day');return true});assert.equal(attempt,1);assert.deepEqual(waits,[]);
 const dailyAgain=transport('daily-limit',async()=>{throw Error('Cooldown must prevent network traffic')});await assert.rejects(()=>dailyAgain(options()),/cooldown is active/);
 attempt=0;const tooLarge=transport('request-too-large',async()=>{attempt++;return Response.json({error:{message:'Request too large on tokens per minute (TPM): Limit 8000, Requested 20000. test-private-key'}},{status:429})});await assert.rejects(()=>tooLarge(options()),e=>{assert.match(e.message,/request exceeds the allowance/);assert.ok(!e.message.includes('test-private-key'));assert.equal(e.details.requestTooLarge,true);return true});assert.equal(attempt,1);
 // Fixing a request-size issue does not leave an unrelated provider cooldown.
 await transport('request-too-large',async()=>completed())(options());
 attempt=0;const unknown=transport('unknown-limit',async()=>{attempt++;return Response.json({error:{message:'Sensitive provider response test-private-key'}},{status:429})});await assert.rejects(()=>unknown(options()),/Check the provider Limits page/);assert.equal(attempt,1);
 const headers=new Headers({'Retry-After':'5'});assert.equal(rateLimitDetails(headers,'not JSON',clock).retryAfterSeconds,5);
 const wrapped=new Error('SDK wrapper',{cause:Object.assign(new Error('Model provider returned HTTP 429: tokens per minute.'),{name:'ModelRateLimitError'})});assert.match(modelLimitError(wrapped).message,/tokens per minute/);
 const controller=new AbortController();const pending=sleepWithSignal(10000,controller.signal);controller.abort(new Error('test cancelled'));await assert.rejects(()=>pending,/test cancelled/);
 console.log('PASS explicit bounded completion tokens, Groq parameter mapping, structured evidence compaction, context/budget checks, persisted per-minute pacing, exact Retry-After handling, daily/oversized quota distinction, redacted diagnostics and cancellation');
}finally{sqlite.close()}
