import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {writeFileSync,unlinkSync} from 'node:fs';
const browser=await chromium.launch({executablePath:'/home/lekha/.agent-browser/browsers/chrome-154.0.8037.92/chrome',args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));const base='http://127.0.0.1:3002',name='Python flow '+Date.now();let process,gateway,agent;const configPath='/tmp/aether-python-flow.json';
try{
 await page.goto(base+'/#python-agents');await page.locator('#loginUser').fill('itops');await page.locator('#loginPassword').fill('itops');await page.locator('#loginSubmit').click();await page.locator('#onboardPython').waitFor();assert.equal(await page.locator('.sim-banner').isVisible(),false);
 gateway=await page.evaluate(async name=>AppAuth.api('/api/infrastructure/gateways',{method:'POST',body:JSON.stringify({name})}),name);
 await page.reload();await page.locator('#onboardPython').click();await page.locator('#pythonName').fill(name);await page.locator('#pythonDescription').fill('Existing Python callable');await page.locator('#pythonGateway').selectOption(gateway.id);await page.locator('#pythonAlias').fill('existing');await page.locator('#onboardPythonForm button').click();await page.getByRole('heading',{name,exact:true}).waitFor();
 const registry=await page.evaluate(()=>AppAuth.api('/api/python-agents'));agent=registry.agents.find(a=>a.name===name);
 writeFileSync(configPath,JSON.stringify({site:base,profiles:{},agents:{existing:{mode:'observe',command:['python3','gateway/python_entrypoint.py','--module','example_agent']}}}));
 process=spawn('python3',['gateway/agent.py','--config',configPath],{env:{...globalThis.process.env,AETHER_GATEWAY_TOKEN:gateway.token,AETHER_GATEWAY_ALLOW_LOCAL_HTTP:'1'},stdio:'ignore'});
 await page.locator('[data-run-agent="'+agent.id+'"]').click();await page.locator('#pythonRunInput').fill('{"task":"inspect current host"}');await page.locator('#pythonRunForm button').click();await page.locator('#detailTitle').getByText('Python run · queued',{exact:true}).waitFor();await page.getByRole('button',{name:'Close details',exact:true}).click();
 await page.getByRole('row').filter({hasText:name}).getByRole('cell',{name:'succeeded',exact:true}).waitFor({timeout:30000});
 const runId=await page.evaluate(async id=>(await AppAuth.api('/api/python-agents')).runs.find(r=>r.agent_id===id&&r.status==='succeeded').id,agent.id);
 await page.reload();await page.locator('#view-python-agents').waitFor({state:'visible'});await page.locator('#detailDialog').waitFor({state:'hidden'});
 const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/runs/'+runId)),page.locator('[data-run-detail="'+runId+'"]').click()]);assert.equal((await response.json()).run.status,'succeeded');
 await page.getByText(/Existing Python callable executed successfully/).waitFor();assert.deepEqual(errors,[]);await page.screenshot({path:'test-results/python-agent-result.png'});
 await page.getByRole('button',{name:'Close details',exact:true}).click();await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.goto(base+'/python-agent-setup');await page.getByRole('heading',{name:'Onboard your existing Python agents'}).waitFor();
 console.log('PASS browser onboarding → queued run → actual Python callable via gateway → persisted JSON result → run drill-down; mobile layout and setup guide; no page errors');
}catch(error){console.log('UI errors',errors);console.log(await page.locator('#detailDialog').evaluate(el=>el.outerHTML));await page.screenshot({path:'test-results/python-failure.png'});throw error}finally{
 if(process)process.kill();if(agent)await page.request.patch(base+'/api/python-agents/'+agent.id,{headers:{Origin:base},data:{enabled:false}});if(gateway)await page.request.delete(base+'/api/infrastructure/gateways/'+gateway.id,{headers:{Origin:base},data:{}});try{unlinkSync(configPath)}catch{}await browser.close();
}
