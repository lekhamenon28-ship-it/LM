import {build} from 'esbuild';
import {resolve} from 'node:path';
import {mkdir, copyFile, cp, rm, readdir, readFile, writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
await mkdir('public/assets',{recursive:true});
await mkdir('public/downloads',{recursive:true});
for(const file of ['agent.py','config.example.json','linux_inventory.py','python_entrypoint.py','example_agent.py','report_execution.py'])await copyFile('gateway/'+file,'public/downloads/'+file);
const featureFiles = (await readdir('app/features')).filter(file=>file.endsWith('.js')).sort();
const featureSources = await Promise.all(featureFiles.map(file=>readFile('app/features/'+file,'utf8')));
await writeFile('public/assets/features.js',featureSources.map((source,index)=>`/* Feature: ${featureFiles[index]} */\n${source}\n`).join('\n'));
execFileSync(process.execPath,['node_modules/tailwindcss/lib/cli.js','-c','tailwind.config.cjs','-i','app/styles.css','-o','public/assets/styles.css','--minify'],{stdio:'inherit'});
await copyFile('node_modules/lucide/dist/umd/lucide.js','public/assets/lucide.js');
for(const name of ['reference','app','platform','auth','login'])await copyFile(`app/${name}.js`,`public/assets/${name}.js`);
await rm('dist',{recursive:true,force:true});
await cp('public','dist/client',{recursive:true});
await mkdir('dist/server',{recursive:true});
await mkdir('dist/.openai',{recursive:true});
const protectedFiles=['index.html','login.html','gateway-setup.html','python-agent-setup.html',...['reference','app','platform','auth','features'].map(name=>'assets/'+name+'.js')];
const protectedAssets={};
for(const file of protectedFiles){protectedAssets['/'+file]={body:await readFile('public/'+file,'utf8'),contentType:file.endsWith('.html')?'text/html; charset=utf-8':'text/javascript; charset=utf-8'};await rm('dist/client/'+file);}
const workerSource=await readFile('worker/index.mjs','utf8');
const bundledWorker='const SITE_ASSETS='+JSON.stringify(protectedAssets)+';\n'+workerSource.replace('async fetch(request,env){', `async fetch(request,env){
  const publicAssets=env.ASSETS;
  env={...env,ASSETS:{fetch:async request=>{const asset=SITE_ASSETS[new URL(request.url).pathname];return asset?new Response(asset.body,{headers:{'Content-Type':asset.contentType}}):publicAssets.fetch(request)}}};`);
await build({stdin:{contents:bundledWorker,resolveDir:resolve('worker'),sourcefile:'site-worker.mjs'},bundle:true,format:'esm',platform:'browser',target:'es2022',outfile:'dist/server/index.js',minify:true});
await copyFile('.openai/hosting.json','dist/.openai/hosting.json');
console.log(`Built app in dist/ with ${featureFiles.length} custom features.`);
