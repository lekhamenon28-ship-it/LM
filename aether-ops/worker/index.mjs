import {makeWorkspace} from './workspace.mjs';
import {makeTelemetry} from './telemetry.mjs';
import {makePythonAgents} from './python-agents.mjs';
import {makeInfrastructure} from './infrastructure.mjs';
const encoder = new TextEncoder();
const ITERATIONS = 100000;
const SESSION_SECONDS = 8 * 60 * 60;
const initialized = new WeakMap();
const schema = [
 `CREATE TABLE IF NOT EXISTS app_users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, salt TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)`,
 `CREATE TABLE IF NOT EXISTS app_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL)`,
 `CREATE INDEX IF NOT EXISTS app_sessions_user ON app_sessions(user_id)`,
 `CREATE TABLE IF NOT EXISTS auth_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, started_at INTEGER NOT NULL)`
];
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2,'0')).join('');
const bytes = text => Uint8Array.from(text.match(/.{2}/g) || [], x => parseInt(x,16));
const random = size => hex(crypto.getRandomValues(new Uint8Array(size)));
async function hashPassword(password, salt) {
 const key = await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveBits']);
 return hex(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:bytes(salt),iterations:ITERATIONS},key,256));
}
async function digest(text) {return hex(await crypto.subtle.digest('SHA-256',encoder.encode(text)))}
function equal(a,b) {if(a.length!==b.length)return false;let diff=0;for(let i=0;i<a.length;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);return diff===0}
async function initialize(db,env={}) {
 if (!db) throw new Error('Authentication database is not configured.');
 if (!initialized.has(db)) {
  const ready=(async()=>{
   for(const sql of schema)await db.prepare(sql).run();
   const existing=await db.prepare('SELECT id FROM app_users LIMIT 1').first();
   if(!existing && env.BOOTSTRAP_ADMIN_PASSWORD){const salt=random(16);const username=env.BOOTSTRAP_ADMIN_USERNAME||'admin';const {password}=credentials({username,password:env.BOOTSTRAP_ADMIN_PASSWORD});await db.prepare('INSERT INTO app_users (id,username,password_hash,salt,role,active,created_at) VALUES (?,?,?,?,?,?,?)').bind('user-'+crypto.randomUUID(),username,await hashPassword(password,salt),salt,'admin',1,new Date().toISOString()).run()}
  })();
  initialized.set(db,ready);
  ready.catch(()=>initialized.delete(db));
 }
 await initialized.get(db);
}
function json(data,status=200,headers={}) {return Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...headers}})}
function cookieName(request){return new URL(request.url).protocol==='https:'?'__Host-aether_session':'aether_session'}
function cookie(request,token,maxAge=SESSION_SECONDS){return `${cookieName(request)}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${new URL(request.url).protocol==='https:'?'; Secure':''}`}
function readCookie(request){const prefix=cookieName(request)+'=';return (request.headers.get('Cookie')||'').split(';').map(s=>s.trim()).find(s=>s.startsWith(prefix))?.slice(prefix.length)}
function publicUser(user){return {id:user.id,username:user.username,role:user.role,active:!!user.active,createdAt:user.created_at}}
async function sessionUser(request,db){const token=readCookie(request);if(!token||!/^[a-f0-9]{64}$/.test(token))return null;return db.prepare('SELECT u.* FROM app_sessions s JOIN app_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1').bind(await digest(token),Date.now()).first()}
async function body(request,limit=4096){if(!request.headers.get('content-type')?.includes('application/json'))throw new Error('Use JSON request data.');const raw=await request.text();if(raw.length>limit)throw new Error('Request is too large.');const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Invalid request data.');return value}
function credentials(value){const username=typeof value.username==='string'?value.username.trim().toLowerCase():'';const password=value.password;if(!/^[a-z0-9][a-z0-9._-]{2,39}$/.test(username))throw new Error('User ID must have 3–40 letters, numbers, dots, hyphens, or underscores.');if(typeof password!=='string'||password.length<12||password.length>128)throw new Error('Password must have 12–128 characters.');return {username,password}}
async function login(request,db){
 const input=await body(request);const username=typeof input.username==='string'?input.username.trim().toLowerCase():'';const password=typeof input.password==='string'?input.password:'';
 if(username.length>100||password.length>128)return json({error:'Invalid user ID or password.'},401);
 const key=await digest('login:'+ (request.headers.get('CF-Connecting-IP')||'local'));
 const attempts=await db.prepare('SELECT * FROM auth_attempts WHERE key=?').bind(key).first();const now=Date.now();
 if(attempts&&now-attempts.started_at<900000&&attempts.count>=10)return json({error:'Too many login attempts. Please try again in 15 minutes.'},429,{'Retry-After':'900'});
 const user=await db.prepare('SELECT * FROM app_users WHERE username=? COLLATE NOCASE').bind(username).first();
 const hashed=await hashPassword(password,user?.salt||'45ba8f94c7b1e6109bbdb8528f9c3dbe');
 if(!user||!user.active||!equal(hashed,user.password_hash)){
  await db.prepare('INSERT INTO auth_attempts (key,count,started_at) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN ?>started_at+900000 THEN 1 ELSE count+1 END, started_at=CASE WHEN ?>started_at+900000 THEN ? ELSE started_at END').bind(key,now,now,now,now).run();
  return json({error:'Invalid user ID or password.'},401);
 }
 await db.prepare('DELETE FROM auth_attempts WHERE key=?').bind(key).run();
 await db.prepare('DELETE FROM app_sessions WHERE expires_at<?').bind(now).run();
 const token=random(32);await db.prepare('INSERT INTO app_sessions (token_hash,user_id,expires_at) VALUES (?,?,?)').bind(await digest(token),user.id,now+SESSION_SECONDS*1000).run();
 return json({user:publicUser(user)},200,{'Set-Cookie':cookie(request,token)});
}
async function api(request,env,path){
 const db=env.DB;await initialize(db,env);
 if(!['GET','HEAD'].includes(request.method)){
  const origin=request.headers.get('Origin');
  if(origin!==new URL(request.url).origin)return json({error:'Request origin is not allowed.'},403);
 }
 if(path==='/api/auth/login'&&request.method==='POST')return login(request,db);
 const user=await sessionUser(request,db);
 if(!user)return json({error:'Please sign in.'},401);
 if(path.startsWith('/api/workspace'))return workspaceApi(request,env,path,user);
 const persona=await workspacePermission(db,user);
 if(!['GET','HEAD'].includes(request.method)&&persona.id==='auditor'&&!path.startsWith('/api/auth/'))return json({error:'Your auditor persona has read-only access.'},403);
 if(path==='/api/telemetry'){await infraInit(db);await initPython(db);return telemetryApi(request,env,path)}
 if(path.startsWith('/api/python-agents')){await infraInit(db);return pythonApi(request,env,path,user)}
 if(path.startsWith('/api/infrastructure'))return infraApi(request,env,path,user);
 if(path==='/api/auth/me'&&request.method==='GET')return json({user:publicUser(user)});
 if(path==='/api/auth/logout'&&request.method==='POST'){
  await db.prepare('DELETE FROM app_sessions WHERE token_hash=?').bind(await digest(readCookie(request))).run();
  return json({ok:true},200,{'Set-Cookie':cookie(request,'',0)});
 }
 if(path==='/api/auth/password'&&request.method==='POST'){
  const input=await body(request);if(typeof input.currentPassword!=='string'||!equal(await hashPassword(input.currentPassword,user.salt),user.password_hash))return json({error:'Current password is incorrect.'},400);
  const {password}=credentials({username:user.username,password:input.password});const salt=random(16);
  await db.prepare('UPDATE app_users SET password_hash=?,salt=? WHERE id=?').bind(await hashPassword(password,salt),salt,user.id).run();
  await db.prepare('DELETE FROM app_sessions WHERE user_id=? AND token_hash<>?').bind(user.id,await digest(readCookie(request))).run();return json({ok:true});
 }
 if(path.startsWith('/api/users')){
  if(user.role!=='admin')return json({error:'Administrator access is required.'},403);
  if(path==='/api/users'&&request.method==='GET'){
   const data=await db.prepare('SELECT id,username,role,active,created_at FROM app_users ORDER BY created_at,username').all();return json({users:data.results.map(publicUser)});
  }
  if(path==='/api/users'&&request.method==='POST'){
   const {username,password}=credentials(await body(request));const existing=await db.prepare('SELECT id FROM app_users WHERE username=? COLLATE NOCASE').bind(username).first();if(existing)return json({error:'This user ID already exists.'},409);
   const id='user-'+crypto.randomUUID();const salt=random(16);const created=new Date().toISOString();
   try{await db.prepare('INSERT INTO app_users (id,username,password_hash,salt,role,active,created_at) VALUES (?,?,?,?,?,?,?)').bind(id,username,await hashPassword(password,salt),salt,'user',1,created).run()}catch(e){if(String(e).includes('UNIQUE'))return json({error:'This user ID already exists.'},409);throw e}
   return json({user:{id,username,role:'user',active:true,createdAt:created}},201);
  }
  const match=path.match(/^\/api\/users\/(user-[a-z0-9-]+)$/);
  if(match&&request.method==='PATCH'){
   const target=await db.prepare('SELECT * FROM app_users WHERE id=?').bind(match[1]).first();if(!target)return json({error:'User was not found.'},404);const input=await body(request);
   if(input.active!==undefined){if(typeof input.active!=='boolean')throw new Error('Account status must be true or false.');if(target.role==='admin'&&!input.active)return json({error:'The administrator account must remain active.'},400);await db.prepare('UPDATE app_users SET active=? WHERE id=?').bind(input.active?1:0,target.id).run();if(!input.active)await db.prepare('DELETE FROM app_sessions WHERE user_id=?').bind(target.id).run()}
   if(input.password!==undefined){const {password}=credentials({username:target.username,password:input.password});const salt=random(16);await db.prepare('UPDATE app_users SET password_hash=?,salt=? WHERE id=?').bind(await hashPassword(password,salt),salt,target.id).run();await db.prepare('DELETE FROM app_sessions WHERE user_id=?').bind(target.id).run()}
   return json({ok:true});
  }
 }
 return json({error:'Endpoint was not found.'},404);
}
function secure(response){const r=new Response(response.body,response);r.headers.set('Cache-Control','no-store');r.headers.set('X-Content-Type-Options','nosniff');r.headers.set('Referrer-Policy','same-origin');r.headers.set('X-Frame-Options','DENY');return r}
const {workspaceApi,workspacePermission}=makeWorkspace({json,body});
const {saveMetrics,telemetryApi,initTelemetry}=makeTelemetry({json});
const {pythonApi,pollPython,resultPython,initPython,recordExternal}=makePythonAgents({json,body,saveMetrics,initTelemetry});
const {infraApi,infraAgent,infraInit}=makeInfrastructure({json,body,digest,random,pollPython,resultPython,recordExternal});
export default {
 async fetch(request,env){
  try{
   const url=new URL(request.url);const path=url.pathname;
   if(path.startsWith('/api/gateway/'))return secure(await infraAgent(request,env,path));
   if(path.startsWith('/api/'))return secure(await api(request,env,path));
   if((['/assets/styles.css','/assets/login.js','/assets/lucide.js','/favicon.ico'].includes(path)||path.startsWith('/assets/fonts/')))return secure(await env.ASSETS.fetch(request));
   if(!['GET','HEAD'].includes(request.method))return json({error:'Method not allowed.'},405);
   await initialize(env.DB,env);const user=await sessionUser(request,env.DB);
   if(!user){if(path.startsWith('/assets/'))return json({error:'Please sign in.'},401);const loginURL=new URL('/login.html',url);return secure(await env.ASSETS.fetch(new Request(loginURL,request)))}
   if(path==='/login'||path==='/login.html')return new Response(null,{status:302,headers:{Location:'/'}});
   const assetURL=new URL(url);if(path==='/')assetURL.pathname='/index.html';if(path==='/gateway-setup')assetURL.pathname='/gateway-setup.html';if(path==='/python-agent-setup')assetURL.pathname='/python-agent-setup.html';let response=await env.ASSETS.fetch(new Request(assetURL,request));
   if(response.status===404&&request.headers.get('Accept')?.includes('text/html'))response=await env.ASSETS.fetch(new Request(new URL('/index.html',url),request));
   if(response.headers.get('Content-Type')?.includes('text/html')){
    const html=(await response.text()).replace('</head>',`<script>window.AETHER_USER=${JSON.stringify(publicUser(user)).replaceAll('<','\\u003c')};</script></head>`);
    response=new Response(html,{status:response.status,headers:response.headers});
   }
   return secure(response);
  }catch(error){console.error('Request failed:',error.message);return json({error:error instanceof SyntaxError?'Invalid JSON data.':/User ID|Password|Request is|request data|Use JSON/.test(error.message)?error.message:'Unable to complete the request. Please try again.'},error instanceof SyntaxError||/User ID|Password|Request is|request data|Use JSON/.test(error.message)?400:500)}
 }
};
