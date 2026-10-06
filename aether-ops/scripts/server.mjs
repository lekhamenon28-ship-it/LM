import {randomBytes} from 'node:crypto';
import http from 'node:http';
import {readFile,stat,writeFile} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
import worker from '../worker/index.mjs';
import {DB} from './local-db.mjs';
if(!process.env.CREDENTIAL_ENCRYPTION_KEY){try{process.env.CREDENTIAL_ENCRYPTION_KEY=(await readFile('.data/credential-key','utf8')).trim()}catch{process.env.CREDENTIAL_ENCRYPTION_KEY=randomBytes(32).toString('hex');await writeFile('.data/credential-key',process.env.CREDENTIAL_ENCRYPTION_KEY,{mode:0o600})}}
const root=resolve(process.env.SERVE_DIST?'dist/client':'public');
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.json':'application/json','.woff2':'font/woff2'};
const ASSETS={async fetch(request){try{const url=new URL(request.url);const file=resolve(root,'.'+decodeURIComponent(url.pathname));if(!file.startsWith(root+sep))return new Response('Forbidden',{status:403});if(!(await stat(file)).isFile())throw Error();return new Response(await readFile(file),{headers:{'Content-Type':mime[extname(file)]||'application/octet-stream'}})}catch{return new Response('Not found',{status:404})}}};
const server=http.createServer(async(req,res)=>{
 try{const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=Buffer.concat(chunks);const request=new Request('http://'+req.headers.host+req.url,{method:req.method,headers:req.headers,...(!['GET','HEAD'].includes(req.method)?{body:data}: {})});const result=await worker.fetch(request,{...process.env,DB,ASSETS});res.writeHead(result.status,Object.fromEntries(result.headers));res.end(req.method==='HEAD'?undefined:Buffer.from(await result.arrayBuffer()))}catch(e){console.error(e);res.writeHead(500);res.end('Server error')}
});
server.listen(Number(process.env.PORT||3000),'0.0.0.0',()=>console.log('AETHER.OPS running at http://localhost:'+(process.env.PORT||3000)));
