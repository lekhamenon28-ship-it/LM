const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const money = (n) => n == null ? 'Pending' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const demoData = {
  '/api/member/': { id: 'AH-4829017', name: 'Maya Thompson', initials: 'MT', plan: 'Aurevia Choice PPO', memberSince: '2022', deductible: { used: 840, total: 1500 }, outOfPocket: { used: 1260, total: 4500 } },
  '/api/claims/': { claims: [
    { id: 'CLM-10842', provider: 'Oak Street Primary Care', service: 'Annual wellness visit', date: 'Aug 8, 2026', status: 'Processed', billed: 245, youPay: 0 },
    { id: 'CLM-10791', provider: 'LabCorp Diagnostics', service: 'Preventive lab panel', date: 'Jul 27, 2026', status: 'Processed', billed: 186, youPay: 22.40 },
    { id: 'CLM-10633', provider: 'Northside Imaging', service: 'Diagnostic imaging', date: 'Jul 10, 2026', status: 'In review', billed: 680, youPay: null }
  ]},
  '/api/care/': { healthScore: 82, tasks: [
    { id: 1, title: 'Schedule annual eye exam', detail: 'Covered at $0 in network', due: 'Recommended', complete: false },
    { id: 2, title: 'Review lab results', detail: 'New results from Jul 27', due: 'New', complete: false },
    { id: 3, title: 'Annual wellness visit', detail: 'Completed Aug 8', due: 'Complete', complete: true }
  ]},
  '/api/providers/': { providers: [
    { name: 'Dr. Priya Raman', specialty: 'Primary Care', distance: '1.2 mi', rating: 4.9, available: 'Today, 3:30 PM' },
    { name: 'Dr. Daniel Cho', specialty: 'Internal Medicine', distance: '2.4 mi', rating: 4.8, available: 'Tomorrow, 9:00 AM' },
    { name: 'Willow Creek Clinic', specialty: 'Urgent Care', distance: '3.1 mi', rating: 4.7, available: 'Walk-ins open' }
  ]}
};
const get = async path => {
  try { const r = await fetch(path); if(!r.ok) throw new Error(); return await r.json(); }
  catch (error) { if (demoData[path]) return demoData[path]; throw error; }
};
let toastTimer;
const toast = msg => { const el=$('#toast'); clearTimeout(toastTimer); el.textContent=msg; el.classList.add('show'); toastTimer=setTimeout(()=>el.classList.remove('show'),2600); };
const openDetail = (title, body, action) => {
  $('#detail-content').innerHTML = `<span class="label">AUREVIA HEALTH</span><h2>${title}</h2><div class="detail-body">${body}</div>${action ? `<button class="primary detail-action">${action}</button>` : ''}`;
  $('#detail-modal').showModal();
};

async function load(){
  try {
    const [member, claims, care] = await Promise.all([get('/api/member/'),get('/api/claims/'),get('/api/care/')]);
    const first=member.name.split(' ')[0];
    $('#first-name').textContent=$('#short-name').textContent=first; $('#avatar').textContent=member.initials;
    $('#plan').textContent=$('#card-plan').textContent=member.plan; $('#member-id').textContent=$('#card-id').textContent=member.id; $('#card-name').textContent=member.name;
    $('#deductible-label').textContent=`${money(member.deductible.used)} of ${money(member.deductible.total)}`;
    $('#oop-label').textContent=`${money(member.outOfPocket.used)} of ${money(member.outOfPocket.total)}`;
    $('#deductible-bar').style.width=`${member.deductible.used/member.deductible.total*100}%`; $('#oop-bar').style.width=`${member.outOfPocket.used/member.outOfPocket.total*100}%`;
    $('#score').textContent=care.healthScore;
    $('#tasks').innerHTML=care.tasks.map(t=>`<button class="task ${t.complete?'done':''}" data-task-id="${t.id}"><span class="task-check">${t.complete?'✓':''}</span><span><b>${t.title}</b><small>${t.detail}</small><em>${t.due}</em></span></button>`).join('');
    $('#claims').innerHTML=claims.claims.map(c=>`<tr tabindex="0" role="button" data-claim-id="${c.id}"><td><b>${c.provider}</b><small>${c.service} · ${c.id}</small></td><td>${c.date}</td><td><span class="status ${c.status==='In review'?'review':''}">${c.status}</span></td><td>${money(c.billed)}</td><td><b>${money(c.youPay)}</b></td></tr>`).join('');
    $('#tasks').onclick=e=>{const card=e.target.closest('[data-task-id]');if(!card)return;const task=care.tasks.find(t=>String(t.id)===card.dataset.taskId);openDetail(task.title,`<p>${task.detail}</p><p><b>Status:</b> ${task.complete?'Completed':'Ready for you'}</p>`,task.complete?'View summary':'Get started');};
    const showClaim=e=>{const row=e.target.closest('[data-claim-id]');if(!row)return;const c=claims.claims.find(item=>item.id===row.dataset.claimId);openDetail('Claim details',`<dl><div><dt>Claim</dt><dd>${c.id}</dd></div><div><dt>Provider</dt><dd>${c.provider}</dd></div><div><dt>Service</dt><dd>${c.service}</dd></div><div><dt>Billed</dt><dd>${money(c.billed)}</dd></div><div><dt>You pay</dt><dd>${money(c.youPay)}</dd></div></dl>`,'Download explanation of benefits');};
    $('#claims').onclick=showClaim; $('#claims').onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-claim-id]')){e.preventDefault();showClaim(e);}};
  } catch { toast('Some health data is temporarily unavailable.'); }
}

const now = new Date();
$('#today').textContent = new Intl.DateTimeFormat('en-US',{weekday:'long',month:'long',day:'numeric'}).format(now).toUpperCase();
$('#greeting').textContent = now.getHours() < 12 ? 'Good morning' : now.getHours() < 18 ? 'Good afternoon' : 'Good evening';

$$('[data-view]').forEach(b=>b.addEventListener('click',()=>{$$('.nav').forEach(n=>n.classList.toggle('active',n.dataset.view===b.dataset.view));const target=b.dataset.view==='claims'?'#claims':b.dataset.view==='care'?'#tasks':b.dataset.view==='find-care'?'#provider-search':'.hello';$(target).scrollIntoView({behavior:'smooth',block:'center'});}));
$('#id-card').onclick=()=>$('#modal').showModal();
$$('dialog .close').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$$('dialog').forEach(dialog=>dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();}));
$('.coverage>.link').onclick=()=>openDetail('Your coverage','<p><b>Aurevia Choice PPO</b> gives you nationwide in-network access without referrals.</p><dl><div><dt>Primary care</dt><dd>$25 copay</dd></div><div><dt>Specialist</dt><dd>$50 copay</dd></div><div><dt>Urgent care</dt><dd>$75 copay</dd></div></dl>','View plan documents');
$('#notifications-button').onclick=()=>{openDetail('Notifications','<div class="notice"><b>Lab results are ready</b><p>Your preventive lab panel from July 27 is available.</p></div><div class="notice"><b>Eye exam reminder</b><p>This preventive visit is covered at $0 in network.</p></div>');$('.dot').hidden=true;};
$('#profile-button').onclick=e=>{e.stopPropagation();const menu=$('#account-menu');const open=menu.classList.toggle('show');e.currentTarget.setAttribute('aria-expanded',open);};
document.addEventListener('click',()=>{$('#account-menu').classList.remove('show');$('#profile-button').setAttribute('aria-expanded','false');});
$('#account-menu').onclick=e=>{e.stopPropagation();const action=e.target.dataset.action;if(!action)return;$('#account-menu').classList.remove('show');if(action==='profile')openDetail('Profile & preferences','<p>Manage contact details, communication choices, and accessibility settings.</p>','Manage profile');else if(action==='support')openDetail('How can we help?','<p>Member support is available Monday–Friday, 8 AM–8 PM.</p><p><b>1-800-555-0198</b></p>','Start secure chat');else toast('This is a demo—no account session was changed.');};
$('#detail-modal').addEventListener('click',e=>{if(e.target.matches('.detail-action')){toast(`${e.target.textContent} selected`);$('#detail-modal').close();}});
$('#provider-search').onsubmit=async e=>{e.preventDefault();const query=e.currentTarget.querySelector('input').value.trim().toLowerCase();const area=$('#providers');$('#clear-search').hidden=!query;area.classList.add('show');area.innerHTML='<div class="skeleton"></div>';try{const d=await get('/api/providers/');const matches=d.providers.filter(p=>!query||`${p.name} ${p.specialty}`.toLowerCase().includes(query));area.innerHTML=matches.length?matches.map((p,i)=>`<button class="provider" data-provider="${i}"><b>${p.name}</b><small>${p.specialty} · ${p.distance} · ★ ${p.rating}</small><em>${p.available}</em><span>View availability →</span></button>`).join(''):'<p class="empty">No exact matches. Try “primary care” or clear your search.</p>';area.onclick=event=>{const card=event.target.closest('[data-provider]');if(!card)return;const p=matches[Number(card.dataset.provider)];openDetail(p.name,`<p>${p.specialty} · ${p.distance} away · ★ ${p.rating}</p><p><b>Next available:</b> ${p.available}</p>`,'Request appointment');};}catch{area.innerHTML='<p class="empty">Unable to load providers right now.</p>';}};
$('#clear-search').onclick=()=>{const form=$('#provider-search');form.querySelector('input').value='';$('#clear-search').hidden=true;form.requestSubmit();form.querySelector('input').focus();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#account-menu').classList.remove('show');$('#profile-button').setAttribute('aria-expanded','false');}});
load();
