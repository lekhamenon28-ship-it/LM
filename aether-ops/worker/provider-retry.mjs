// Retry throttled requests once, without extending the caller's deadline.
export async function fetchWithThrottleRetry(url,options){
 let response=await fetch(url,options);
 if(response.status!==429)return response;
 const raw=response.headers.get('Retry-After');
 const seconds=raw===null?1:Number(raw);
 const delay=Number.isFinite(seconds)?Math.max(0,seconds*1000):Math.max(0,Date.parse(raw)-Date.now());
 // Longer cooldowns are reported to the operator, never retried early.
 if(!Number.isFinite(delay)||delay>2000)return response;
 await response.body?.cancel();
 await new Promise((resolve,reject)=>{if(options.signal?.aborted){reject(options.signal.reason);return}const done=()=>{options.signal?.removeEventListener('abort',abort);resolve()};const timer=setTimeout(done,delay);const abort=()=>{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);reject(options.signal.reason)};options.signal?.addEventListener('abort',abort,{once:true})});
 options.signal?.throwIfAborted();
 return fetch(url,options);
}
