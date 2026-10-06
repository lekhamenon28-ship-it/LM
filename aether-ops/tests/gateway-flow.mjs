import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {writeFileSync,unlinkSync} from 'node:fs';
const browser=await chromium.launch({executablePath:'/home/lekha/.agent-browser/browsers/chrome-154.0.8037.92/chrome',args:['--no-sandbox']});
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));const base='http://127.0.0.1:3001';const gatewayName='Gateway flow '+Date.now();let agent,conn,gw;
try{
 await page.goto(base+'/#infrastructure');await page.locator('#loginUser').fill('itops');await page.locator('#loginPassword').fill('itops');await page.locator('#loginSubmit').click();await page.locator('#pairGateway').waitFor();
 await page.locator('#pairGateway').click();await page.locator('#gatewayName').fill(gatewayName);await page.locator('#gatewayForm button').click();const token=await page.locator('textarea[aria-label="Gateway token"]').inputValue();await page.getByRole('button',{name:'Close details',exact:true}).click();
 const state=await page.evaluate(()=>AppAuth.api('/api/infrastructure'));gw=state.gateways.find(x=>x.name===gatewayName);assert.ok(gw);
 await page.locator('#addInfra').click();await page.locator('#infraName').fill('Real local Linux');await page.locator('#infraProduct').selectOption('linux');await page.locator('#infraGateway').selectOption(gw.id);await page.locator('#infraRef').fill('linux_local');await page.locator('#infraForm button').click();await page.getByText('Real local Linux',{exact:true}).waitFor();
 writeFileSync('/tmp/aether-gateway-test.json',JSON.stringify({site:base,profiles:{linux_local:{product:'linux',command:['python3','gateway/linux_inventory.py']}}}));
 agent=spawn('python3',['gateway/agent.py','--config','/tmp/aether-gateway-test.json'],{env:{...process.env,AETHER_GATEWAY_TOKEN:token,AETHER_GATEWAY_ALLOW_LOCAL_HTTP:'1'},stdio:'ignore'});
 const s=await page.evaluate(()=>AppAuth.api('/api/infrastructure'));conn=s.connections.find(x=>x.name==='Real local Linux');await page.locator('[data-action="inventory"][data-id="'+conn.id+'"]').click();
 await page.locator('article').filter({hasText:'Real local Linux'}).getByText('Connected',{exact:true}).waitFor({timeout:30000});
 await page.reload();await page.locator('[data-detail="'+conn.id+'"]').click();await page.getByText('Root filesystem',{exact:true}).waitFor();assert.deepEqual(errors,[]);await page.screenshot({path:'test-results/infrastructure-inventory.png'});
 console.log('PASS browser registration → server jobs → Python gateway → actual local Linux inventory → saved snapshot → drill-down; no page errors');
}finally{
 if(agent)agent.kill();if(conn)await page.evaluate(id=>AppAuth.api('/api/infrastructure/connections/'+id,{method:'DELETE',body:'{}'}),conn.id);if(gw)await page.evaluate(id=>AppAuth.api('/api/infrastructure/gateways/'+id,{method:'DELETE',body:'{}'}),gw.id);try{unlinkSync('/tmp/aether-gateway-test.json')}catch{}await browser.close();
}
