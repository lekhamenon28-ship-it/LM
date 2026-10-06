import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {homedir} from 'node:os';
const fallback=homedir()+'/.agent-browser/browsers/chrome-154.0.8037.92/chrome';
const browser=await chromium.launch({executablePath:process.env.BROWSER_PATH||(existsSync(fallback)?fallback:undefined),args:['--no-sandbox']});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const sample=await readFile('examples/features/operations-notes.js','utf8');
await context.route('**/assets/features.js',route=>route.fulfill({contentType:'text/javascript',body:sample}));
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 await page.goto('http://localhost:3000/#operations-notes');
 await page.locator('#loginUser').fill(process.env.TEST_USER||'itops');await page.locator('#loginPassword').fill(process.env.TEST_PASSWORD||'itops');await page.locator('#loginSubmit').click();
 await page.locator('#operations-notes-text').waitFor();
 assert.equal(await page.locator('.tab-view:not(.hidden)').getAttribute('id'),'view-operations-notes');
 console.log('PASS Feature registration, sidebar, and direct route');
 await page.locator('#operations-notes-text').fill('Investigate checkout latency');
 await page.getByRole('button',{name:'Save notes',exact:true}).click();
 await page.reload();
 assert.equal(await page.locator('#operations-notes-text').inputValue(),'');
 console.log('PASS Extension scratch state stays session-only; operational data belongs in backend APIs');
 assert.equal(await page.evaluate(()=>{const agents=Aether.getAgents();agents.push({id:'test'});return Aether.getAgents().length}),0);
 console.log('PASS Shared API returns snapshots without modifying core agents');
 assert.equal(await page.evaluate(async()=>{
  Aether.services.register('test.echo',async input=>({echo:input}));
  const output=await Aether.services.call('test.echo','hello');
  try{await Aether.services.call('missing.service')}catch(error){return output.echo==='hello'&&error.message.includes('not configured')}
  return false;
 }),true);
 console.log('PASS Async service adapters and unconfigured service errors');
 assert.equal(await page.evaluate(()=>{
  try{Aether.registerFeature({id:'operations-notes',label:'Duplicate',mount(){}})}catch(error){return error.message.includes('already registered')}
  return false;
 }),true);
 console.log('PASS Duplicate feature IDs rejected');
 await page.evaluate(()=>history.pushState(null,'','#other'));await page.goBack();
 await page.locator('#operations-notes-text').waitFor();
 console.log('PASS Custom section browser back navigation');
 assert.deepEqual(errors,[]);
 console.log('7 extension checks passed.');
}finally{await browser.close()}
